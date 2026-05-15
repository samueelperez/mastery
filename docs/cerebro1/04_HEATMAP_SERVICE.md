# 04 — HeatmapService (Aggregator)

<context>
The service is the brain of the brain. It calls all enabled providers in parallel, normalizes their outputs onto a common price grid, merges them with adaptive weights, computes derived metrics (imbalance, density, agreement), and produces a single `HeatmapSnapshot`.

Default behavior in M1: uniform weights (1/N per active provider). After M2 calibration, weights are read from `liquidation_provider_weights` table.
</context>

<deliverables>
- `apps/api/app/liquidation/service.py` — `HeatmapService` and `Repo` collaborator.
- `apps/api/app/liquidation/repo.py` — DB CRUD: `persist_snapshot`, `fetch_weights`, `latest_snapshot`.
- `apps/api/app/liquidation/calibration.py` — `compute_provider_weights` (used by M2 weekly job).
- `apps/api/tests/liquidation/test_service.py` — unit tests for merge, normalization, agreement.
- `apps/api/tests/liquidation/test_repo.py` — DB persistence tests (transactional rollback).
- `apps/api/tests/liquidation/test_calibration.py` — weight computation tests.
</deliverables>

<file_apps_api_app_liquidation_repo_py>

```python
"""Repository: persistence of heatmap snapshots and provider weights.

Uses raw SQL via SQLAlchemy `text()` for clarity (this module doesn't have
the volume to need ORM). All queries are scoped by `user_id`.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.liquidation.models import (
    HeatmapSnapshot,
    ProviderName,
    ProviderWeight,
    RawProviderBucket,
    TimeframeLiteral,
)


class LiquidationRepo:
    """All DB access for the liquidation module."""

    def __init__(self, session: AsyncSession, user_id: str) -> None:
        self._session = session
        self._user_id = user_id

    async def persist_snapshot(
        self,
        snapshot: HeatmapSnapshot,
        raw_buckets_by_source: dict[ProviderName, list[RawProviderBucket]],
    ) -> None:
        """Persist all raw buckets (one row per source per bucket) for the
        snapshot. The aggregated snapshot itself is not persisted as a row —
        it's derived on read by querying buckets and re-aggregating.

        This design choice keeps storage cheaper and lets us re-aggregate
        with different weights retroactively if calibration changes.
        """
        # Persist raw buckets, one row per (source, bucket).
        rows: list[dict] = []
        for source, buckets in raw_buckets_by_source.items():
            for b in buckets:
                rows.append({
                    "user_id": self._user_id,
                    "symbol": snapshot.symbol,
                    "timeframe": snapshot.timeframe,
                    "snapshot_ts": snapshot.as_of,
                    "price_low": b.price_low,
                    "price_high": b.price_high,
                    "side": b.side,
                    "est_volume_usd": b.est_volume_usd,
                    "source": source,
                })

        if not rows:
            return

        await self._session.execute(
            text("""
                INSERT INTO liquidation_buckets (
                    user_id, symbol, timeframe, snapshot_ts,
                    price_low, price_high, side, est_volume_usd, source
                )
                VALUES (
                    :user_id, :symbol, :timeframe, :snapshot_ts,
                    :price_low, :price_high, :side, :est_volume_usd, :source
                )
            """),
            rows,
        )
        await self._session.commit()

    async def fetch_weights(
        self,
        symbol: str,
        timeframe: TimeframeLiteral,
    ) -> dict[ProviderName, float]:
        """Return the most recent computed weights for the (symbol, tf) cell.

        Returns empty dict if no weights have been computed yet (M1 default).
        The service interprets empty as "use uniform weights".
        """
        result = await self._session.execute(
            text("""
                SELECT DISTINCT ON (provider) provider, weight
                FROM liquidation_provider_weights
                WHERE symbol = :symbol AND timeframe = :timeframe
                ORDER BY provider, computed_at DESC
            """),
            {"symbol": symbol, "timeframe": timeframe},
        )
        return {row.provider: float(row.weight) for row in result}

    async def save_weights(self, weights: Iterable[ProviderWeight]) -> None:
        rows = [
            {
                "symbol": w.symbol,
                "timeframe": w.timeframe,
                "provider": w.provider,
                "weight": w.weight,
                "agreement_rate": w.agreement_rate,
                "n_samples": w.n_samples,
                "computed_at": w.computed_at,
            }
            for w in weights
        ]
        if not rows:
            return
        await self._session.execute(
            text("""
                INSERT INTO liquidation_provider_weights
                  (symbol, timeframe, provider, weight, agreement_rate, n_samples, computed_at)
                VALUES
                  (:symbol, :timeframe, :provider, :weight, :agreement_rate, :n_samples, :computed_at)
            """),
            rows,
        )
        await self._session.commit()

    async def fetch_agreement_log(
        self,
        *,
        since: datetime | None = None,
        symbol: str | None = None,
        timeframe: TimeframeLiteral | None = None,
    ) -> list[dict]:
        """Read rows from liquidation_agreement_log. Used by calibration."""
        clauses = ["user_id = :uid"]
        params: dict = {"uid": self._user_id}
        if since:
            clauses.append("logged_at >= :since")
            params["since"] = since
        if symbol:
            clauses.append("symbol = :symbol")
            params["symbol"] = symbol
        if timeframe:
            clauses.append("timeframe = :timeframe")
            params["timeframe"] = timeframe
        where = " AND ".join(clauses)
        result = await self._session.execute(
            text(f"""
                SELECT symbol, timeframe, proposed_zone_price, proposed_zone_side,
                       source_a_price, source_b_price, source_c_verdict,
                       delta_a_pct, delta_b_pct, logged_at
                FROM liquidation_agreement_log
                WHERE {where}
                ORDER BY logged_at DESC
            """),
            params,
        )
        return [dict(r._mapping) for r in result]
```
</file_apps_api_app_liquidation_repo_py>

<file_apps_api_app_liquidation_service_py>

```python
"""HeatmapService — aggregates multiple providers into a single HeatmapSnapshot.

Default: uniform weights across enabled providers. Once M2 calibration has
been run, weights are loaded from `liquidation_provider_weights`.
"""
from __future__ import annotations

import asyncio
import logging
import math
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from typing import Final

from app.agent.tools._envelope import Provenance
from app.liquidation.models import (
    ConfidenceLiteral,
    HeatmapSnapshot,
    MagnetZone,
    ProviderHeatmap,
    ProviderName,
    RawProviderBucket,
    SideLiteral,
    TimeframeLiteral,
)
from app.liquidation.providers.base import BaseLiquidationProvider
from app.liquidation.repo import LiquidationRepo

LOG = logging.getLogger(__name__)

# Provider timeout per call.
PROVIDER_TIMEOUT_S: Final[float] = 3.0

# Bucket size as fraction of current price (must match providers for clean merge).
BUCKET_PCT: Final[float] = 0.0025

# Per-bucket filter threshold (fraction of total est_volume).
MIN_BUCKET_FRACTION: Final[float] = 0.005

# Floor for adaptive weights (no provider dies permanently).
WEIGHT_FLOOR: Final[float] = 0.10

# Distance band for imbalance computation (±5% of price).
IMBALANCE_BAND_PCT: Final[float] = 5.0

# Distance band for density computation (±2% of price).
DENSITY_BAND_PCT: Final[float] = 2.0

# Confidence thresholds for sources_agreement.
HIGH_AGREEMENT: Final[float] = 0.85
MEDIUM_AGREEMENT: Final[float] = 0.60


class HeatmapService:
    """Orchestrates provider calls, merging, and snapshot persistence."""

    def __init__(
        self,
        providers: list[BaseLiquidationProvider],
        repo: LiquidationRepo,
    ) -> None:
        self._providers = [p for p in providers if p.enabled]
        self._repo = repo

    async def get_snapshot(
        self,
        symbol: str,
        timeframe: TimeframeLiteral,
        current_price: float,
        max_distance_pct: float = 10.0,
    ) -> HeatmapSnapshot:
        now = datetime.now(tz=UTC)

        # 1. Collect provider outputs in parallel with timeout.
        provider_results = await self._call_providers(
            symbol=symbol,
            timeframe=timeframe,
            current_price=current_price,
            max_distance_pct=max_distance_pct,
        )

        warnings: list[str] = []
        active_outputs: list[ProviderHeatmap] = []
        for p, result in provider_results:
            if isinstance(result, Exception):
                warnings.append(f"provider_failed:{p.name}")
                continue
            if self._is_stale(result, max_age_s=p.max_age_seconds, now=now):
                warnings.append(f"provider_stale:{p.name}")
                continue
            active_outputs.append(result)

        if len(active_outputs) < 2:
            warnings.append("degraded_few_sources")

        if not active_outputs:
            return self._empty_snapshot(
                symbol=symbol,
                timeframe=timeframe,
                current_price=current_price,
                now=now,
                warnings=warnings + ["no_active_providers"],
            )

        # 2. Load weights (uniform if no calibration yet).
        weights = await self._load_weights(symbol, timeframe, active_outputs)

        # 3. Normalize to common grid and merge.
        merged_zones = self._merge(
            outputs=active_outputs,
            weights=weights,
            current_price=current_price,
        )

        # 4. Compute derived metrics.
        imbalance = self._imbalance(merged_zones, current_price)
        density = self._density(merged_zones, current_price)
        agreement = self._agreement(active_outputs, current_price)
        nearest_long, nearest_short = self._nearest(merged_zones, current_price)
        confidence = self._confidence_for_zone(agreement)

        # Override per-zone confidence with the global agreement-derived one.
        merged_zones = [
            z.model_copy(update={"confidence": confidence}) for z in merged_zones
        ]
        if nearest_long:
            nearest_long = nearest_long.model_copy(update={"confidence": confidence})
        if nearest_short:
            nearest_short = nearest_short.model_copy(update={"confidence": confidence})

        snapshot = HeatmapSnapshot(
            symbol=symbol,
            timeframe=timeframe,
            current_price=current_price,
            as_of=now,
            magnet_zones=merged_zones,
            nearest_long_liq=nearest_long,
            nearest_short_liq=nearest_short,
            imbalance_ratio=imbalance,
            cluster_density=density,
            sources_used=[o.provider for o in active_outputs],
            sources_agreement=agreement,
            provenance=Provenance(
                source="liquidation_heatmap_engine",
                as_of=now,
                rows=len(merged_zones),
                warnings=warnings,
            ),
        )

        # 5. Persist raw buckets.
        raw_by_source: dict[ProviderName, list[RawProviderBucket]] = {
            o.provider: o.buckets for o in active_outputs
        }
        try:
            await self._repo.persist_snapshot(snapshot, raw_by_source)
        except Exception:
            LOG.exception("persist_snapshot_failed",
                          extra={"symbol": symbol, "timeframe": timeframe})
            # Persistence is best-effort; the snapshot itself is still returned.

        return snapshot

    # ----------------------------------------------------------
    # Internals
    # ----------------------------------------------------------
    async def _call_providers(
        self,
        symbol: str,
        timeframe: TimeframeLiteral,
        current_price: float,
        max_distance_pct: float,
    ) -> list[tuple[BaseLiquidationProvider, ProviderHeatmap | Exception]]:
        """Call all enabled providers in parallel with a hard timeout."""
        async def _call(p: BaseLiquidationProvider) -> ProviderHeatmap | Exception:
            try:
                return await asyncio.wait_for(
                    p.get_heatmap(symbol, timeframe, current_price, max_distance_pct),
                    timeout=PROVIDER_TIMEOUT_S,
                )
            except (asyncio.TimeoutError, Exception) as e:
                return e

        results = await asyncio.gather(*(_call(p) for p in self._providers))
        return list(zip(self._providers, results))

    def _is_stale(
        self,
        result: ProviderHeatmap,
        max_age_s: int,
        now: datetime,
    ) -> bool:
        age = (now - result.as_of).total_seconds()
        return age > max_age_s

    async def _load_weights(
        self,
        symbol: str,
        timeframe: TimeframeLiteral,
        active: list[ProviderHeatmap],
    ) -> dict[ProviderName, float]:
        stored = await self._repo.fetch_weights(symbol, timeframe)
        if not stored:
            # M1 default: uniform.
            n = len(active)
            return {o.provider: 1.0 / n for o in active}

        # Restrict to active providers and renormalize.
        relevant = {
            o.provider: stored.get(o.provider, WEIGHT_FLOOR)
            for o in active
        }
        total = sum(relevant.values()) or 1.0
        return {k: v / total for k, v in relevant.items()}

    def _merge(
        self,
        outputs: list[ProviderHeatmap],
        weights: dict[ProviderName, float],
        current_price: float,
    ) -> list[MagnetZone]:
        """Sum weighted contributions into a common bucket grid keyed by
        (price_low, side)."""
        # Use the bucket_size of the first provider's buckets as canonical;
        # all providers should use the same BUCKET_PCT. If they don't, snap to
        # service's BUCKET_PCT.
        bucket_size = current_price * BUCKET_PCT

        merged: dict[tuple[float, SideLiteral], dict] = defaultdict(
            lambda: {"weight_sum": 0.0, "breakdown": defaultdict(float)}
        )

        for o in outputs:
            w = weights.get(o.provider, 0.0)
            if w == 0.0:
                continue
            for b in o.buckets:
                # Snap to canonical grid.
                snapped_low = math.floor(b.price_low / bucket_size) * bucket_size
                key = (snapped_low, b.side)
                contribution = b.est_volume_usd * w
                merged[key]["weight_sum"] += contribution
                merged[key]["breakdown"][o.provider] += contribution

        if not merged:
            return []

        total = sum(e["weight_sum"] for e in merged.values())
        threshold = total * MIN_BUCKET_FRACTION

        zones: list[MagnetZone] = []
        for (price_low, side), entry in merged.items():
            if entry["weight_sum"] < threshold:
                continue
            price_high = price_low + bucket_size
            mid = (price_low + price_high) / 2
            distance_pct = (mid - current_price) / current_price * 100.0
            zones.append(MagnetZone(
                price_low=price_low,
                price_high=price_high,
                side=side,
                est_volume_usd=entry["weight_sum"],
                distance_pct=distance_pct,
                source_breakdown=dict(entry["breakdown"]),
                confidence="medium",  # overridden by caller with agreement-derived
            ))

        zones.sort(key=lambda z: abs(z.distance_pct))
        return zones

    def _imbalance(self, zones: list[MagnetZone], current_price: float) -> float:
        band = current_price * IMBALANCE_BAND_PCT / 100.0
        long_vol = sum(
            z.est_volume_usd for z in zones
            if z.side == "long_liq" and abs(z.distance_pct) * current_price / 100.0 <= band
        )
        short_vol = sum(
            z.est_volume_usd for z in zones
            if z.side == "short_liq" and abs(z.distance_pct) * current_price / 100.0 <= band
        )
        if short_vol == 0 and long_vol == 0:
            return 1.0
        if short_vol == 0:
            return float("inf")
        return long_vol / short_vol

    def _density(self, zones: list[MagnetZone], current_price: float) -> float:
        if not zones:
            return 0.0
        band_pct = DENSITY_BAND_PCT
        total = sum(z.est_volume_usd for z in zones)
        if total == 0:
            return 0.0
        near = sum(
            z.est_volume_usd for z in zones if abs(z.distance_pct) <= band_pct
        )
        return min(1.0, near / total)

    def _agreement(
        self,
        outputs: list[ProviderHeatmap],
        current_price: float,
    ) -> float:
        """1 - coefficient of variation over the top-5 buckets across providers.

        Strategy: for each provider, take its top-5 buckets by volume. Compare
        the position (price_low) of each rank-i bucket across providers.
        Compute CV per rank; average and convert to agreement.
        """
        if len(outputs) < 2:
            return 1.0  # only one provider; trivially "agrees with itself"

        # Per-provider top-5 sorted lists.
        top_5_per_provider: list[list[RawProviderBucket]] = [
            sorted(o.buckets, key=lambda b: -b.est_volume_usd)[:5]
            for o in outputs
        ]

        # Need at least 2 providers with non-empty top-5.
        valid = [t for t in top_5_per_provider if t]
        if len(valid) < 2:
            return 0.5  # neutral

        # Compare rank-i positions.
        cvs: list[float] = []
        for rank in range(min(len(t) for t in valid)):
            prices = [t[rank].price_low for t in valid]
            mean = statistics.mean(prices)
            if mean == 0:
                continue
            stdev = statistics.pstdev(prices)
            cv = stdev / mean
            cvs.append(cv)

        if not cvs:
            return 0.5
        avg_cv = statistics.mean(cvs)
        # Map CV to [0,1] agreement: CV=0 -> agreement=1; CV>=0.05 -> agreement->0.
        # Empirically: prices within 0.5% across providers is high agreement.
        agreement = max(0.0, min(1.0, 1.0 - avg_cv * 20.0))
        return agreement

    def _confidence_for_zone(self, agreement: float) -> ConfidenceLiteral:
        if agreement >= HIGH_AGREEMENT:
            return "high"
        if agreement >= MEDIUM_AGREEMENT:
            return "medium"
        return "low"

    def _nearest(
        self,
        zones: list[MagnetZone],
        current_price: float,
    ) -> tuple[MagnetZone | None, MagnetZone | None]:
        nearest_long = None
        nearest_short = None
        for z in zones:
            if z.side == "long_liq" and z.distance_pct < 0:
                if nearest_long is None or abs(z.distance_pct) < abs(nearest_long.distance_pct):
                    nearest_long = z
            elif z.side == "short_liq" and z.distance_pct > 0:
                if nearest_short is None or abs(z.distance_pct) < abs(nearest_short.distance_pct):
                    nearest_short = z
        return nearest_long, nearest_short

    def _empty_snapshot(
        self,
        symbol: str,
        timeframe: TimeframeLiteral,
        current_price: float,
        now: datetime,
        warnings: list[str],
    ) -> HeatmapSnapshot:
        return HeatmapSnapshot(
            symbol=symbol,
            timeframe=timeframe,
            current_price=current_price,
            as_of=now,
            magnet_zones=[],
            nearest_long_liq=None,
            nearest_short_liq=None,
            imbalance_ratio=1.0,
            cluster_density=0.0,
            sources_used=[],
            sources_agreement=0.0,
            provenance=Provenance(
                source="liquidation_heatmap_engine",
                as_of=now,
                rows=0,
                warnings=warnings,
            ),
        )
```
</file_apps_api_app_liquidation_service_py>

<file_apps_api_app_liquidation_calibration_py>

```python
"""Calibration job: compute provider weights from agreement log.

Run weekly from M2 onward (cron task or manual trigger). Output goes to
`liquidation_provider_weights`. The service reads this table to weight
provider outputs.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from app.liquidation.models import ProviderName, ProviderWeight, TimeframeLiteral
from app.liquidation.repo import LiquidationRepo

# Floor weight: no provider falls below 10% even if agreement is 0.
WEIGHT_FLOOR: float = 0.10

# Rolling window of agreement_log entries to consider.
ROLLING_WINDOW_DAYS: int = 30

# Minimum samples per (symbol, tf, provider) to compute a meaningful weight.
MIN_SAMPLES: int = 10


async def compute_provider_weights(
    repo: LiquidationRepo,
    *,
    now: datetime | None = None,
) -> list[ProviderWeight]:
    """Compute weights from the operator's TradingDifferent verdicts.

    For each (symbol, timeframe, provider):
      1. Filter agreement log to entries where source_c_verdict in {'agree','close','disagree'}.
      2. Count where delta_<provider>_pct < 0.5% (provider agrees with proposed zone).
      3. agreement_rate = agree_count / total.
      4. weight = max(WEIGHT_FLOOR, agreement_rate); then normalize per (symbol, tf).

    Returns a list of `ProviderWeight` to be persisted by the caller.
    """
    now = now or datetime.now(tz=UTC)
    since = now - timedelta(days=ROLLING_WINDOW_DAYS)
    rows = await repo.fetch_agreement_log(since=since)

    # Bucket by (symbol, timeframe).
    by_cell: dict[tuple[str, TimeframeLiteral], list[dict]] = defaultdict(list)
    for r in rows:
        if r["source_c_verdict"] in ("skipped",):
            continue
        by_cell[(r["symbol"], r["timeframe"])].append(r)

    out: list[ProviderWeight] = []
    for (symbol, tf), entries in by_cell.items():
        # Compute per-provider agreement.
        per_provider: dict[ProviderName, list[bool]] = defaultdict(list)
        for e in entries:
            # "agree" with ground truth means our proposed zone matches TD,
            # and the provider's price was close to the proposed zone.
            td_agrees = e["source_c_verdict"] in ("agree", "close")
            for provider, delta_key in (("A_derived", "delta_a_pct"), ("B_hyperliquid", "delta_b_pct")):
                d = e.get(delta_key)
                if d is None:
                    continue
                provider_agrees = abs(float(d)) <= 0.5
                # Sample: provider got it right when TD also agreed.
                per_provider[provider].append(td_agrees and provider_agrees)

        # Compute raw rates.
        raw: dict[ProviderName, tuple[float, int]] = {}
        for prov, samples in per_provider.items():
            if len(samples) < MIN_SAMPLES:
                continue
            raw[prov] = (sum(samples) / len(samples), len(samples))

        if not raw:
            continue

        # Floor + normalize.
        floored = {prov: max(WEIGHT_FLOOR, rate) for prov, (rate, _) in raw.items()}
        total = sum(floored.values()) or 1.0
        normalized = {prov: w / total for prov, w in floored.items()}

        for prov, weight in normalized.items():
            rate, n = raw[prov]
            out.append(ProviderWeight(
                symbol=symbol,
                timeframe=tf,
                provider=prov,
                weight=weight,
                agreement_rate=rate,
                n_samples=n,
                computed_at=now,
            ))

    return out
```
</file_apps_api_app_liquidation_calibration_py>

<gotchas>
- The service does NOT persist `HeatmapSnapshot` rows; it persists raw buckets per source. Reconstructing the merged snapshot from buckets is cheap and lets us re-aggregate with new weights without re-fetching from providers.
- `_merge` snaps every provider's bucket boundaries to a canonical grid. Providers SHOULD already use the same `BUCKET_PCT`, but if a future provider differs, snapping prevents grid misalignment from inflating buckets.
- `_agreement` requires at least 2 providers with non-empty top-5. With 1 provider we return 1.0 (trivially agrees with itself) — but `provenance.warnings` will already contain `degraded_few_sources`.
- `imbalance` returns `float('inf')` if there's long liquidity but zero short liquidity. Downstream code must handle this — typically by capping at e.g. 10.0 when displaying. The model accepts inf because pydantic v2 allows it for float; if you want a finite ceiling, cap in the service.
- `_load_weights` falls back to uniform when no row exists. This is the M1 path. Don't add a "wait for calibration" gate — the system should work day 1 without any agreement data.
- `WEIGHT_FLOOR = 0.10`: also enforced at the DB check constraint level. Both must stay in sync.
- Calibration ignores `'skipped'` verdicts (operator didn't respond in time). Don't count them as disagreements.
- `MIN_SAMPLES = 10` per (symbol, tf, provider) before computing a weight. Below that, we keep using uniform. Document this in the spec for the operator's expectations.
- `defaultdict(lambda: {"weight_sum": 0.0, "breakdown": defaultdict(float)})` creates nested defaultdicts. When persisted to DB or serialized, force `dict(entry["breakdown"])` — pydantic will fail on `defaultdict` if it expects `dict`.
</gotchas>

<acceptance>
- [ ] `HeatmapService.get_snapshot(...)` returns a valid `HeatmapSnapshot` when at least one provider works.
- [ ] Returns empty snapshot with `no_active_providers` warning when all providers fail.
- [ ] `degraded_few_sources` warning appears when only 1 provider is alive.
- [ ] Stale providers (older than their `max_age_seconds`) are excluded.
- [ ] Uniform weights used when `liquidation_provider_weights` table is empty.
- [ ] Stored weights used when present; uniformized over only the active providers.
- [ ] `imbalance_ratio`, `cluster_density`, `sources_agreement` computed correctly (unit tests with synthetic inputs).
- [ ] `nearest_long_liq` only set if there's a `long_liq` zone below current price.
- [ ] `nearest_short_liq` only set if there's a `short_liq` zone above current price.
- [ ] Raw buckets persisted to `liquidation_buckets` on successful snapshot.
- [ ] `compute_provider_weights` produces weights >= `WEIGHT_FLOOR` even for 0% agreement.
- [ ] Weights per (symbol, tf) sum to 1.0 after normalization.
- [ ] `MIN_SAMPLES` gate skips low-data cells.
</acceptance>
