"""HeatmapService — aggregates multiple providers into a HeatmapSnapshot.

Default: uniform weights across enabled providers (M1). Once M2 calibration
has been run, weights come from `liquidation_provider_weights` via repo.
"""

from __future__ import annotations

import asyncio
import logging
import math
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, Final

from app.agent.tools._envelope import Provenance
from app.core.observability.metrics import (
    liq_provider_errors_total,
    liq_snapshot_latency_seconds,
    liq_snapshots_total,
)
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

# Floor for adaptive weights (no provider dies permanently). Mirrors DB CHECK.
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

        provider_results = await self._call_providers(
            symbol=symbol,
            timeframe=timeframe,
            current_price=current_price,
            max_distance_pct=max_distance_pct,
        )

        warnings: list[str] = []
        active_outputs: list[ProviderHeatmap] = []
        for p, result in provider_results:
            if isinstance(result, BaseException):
                warnings.append(f"provider_failed:{p.name}")
                kind = "timeout" if isinstance(result, asyncio.TimeoutError) else "exception"
                liq_provider_errors_total.labels(provider=p.name, kind=kind).inc()
                continue
            if self._is_stale(result, max_age_s=p.max_age_seconds, now=now):
                warnings.append(f"provider_stale:{p.name}")
                liq_provider_errors_total.labels(provider=p.name, kind="stale").inc()
                continue
            active_outputs.append(result)

        if not active_outputs:
            liq_snapshots_total.labels(symbol=symbol, timeframe=timeframe, outcome="empty").inc()
            return self._empty_snapshot(
                symbol=symbol,
                timeframe=timeframe,
                current_price=current_price,
                now=now,
                warnings=[*warnings, "no_active_providers"],
            )

        if len(active_outputs) < 2:
            warnings.append("degraded_few_sources")

        weights = await self._load_weights(symbol, timeframe, active_outputs)

        merged_zones = self._merge(
            outputs=active_outputs,
            weights=weights,
            current_price=current_price,
        )

        imbalance = self._imbalance(merged_zones, current_price)
        density = self._density(merged_zones, current_price)
        agreement = self._agreement(active_outputs, current_price)
        nearest_long, nearest_short = self._nearest(merged_zones, current_price)
        confidence = self._confidence_for_zone(agreement)

        merged_zones = [z.model_copy(update={"confidence": confidence}) for z in merged_zones]
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

        # Persist raw buckets (best-effort; service still returns snapshot on
        # persist failure).
        raw_by_source: dict[ProviderName, list[RawProviderBucket]] = {
            o.provider: o.buckets for o in active_outputs
        }
        try:
            await self._repo.persist_snapshot(snapshot, raw_by_source)
        except Exception:
            LOG.exception(
                "persist_snapshot_failed",
                extra={"symbol": symbol, "timeframe": timeframe},
            )

        outcome = "degraded" if len(active_outputs) < 2 else "ok"
        liq_snapshots_total.labels(symbol=symbol, timeframe=timeframe, outcome=outcome).inc()

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
    ) -> list[tuple[BaseLiquidationProvider, ProviderHeatmap | BaseException]]:
        async def _call(
            p: BaseLiquidationProvider,
        ) -> ProviderHeatmap | BaseException:
            start = asyncio.get_event_loop().time()
            try:
                return await asyncio.wait_for(
                    p.get_heatmap(symbol, timeframe, current_price, max_distance_pct),
                    timeout=PROVIDER_TIMEOUT_S,
                )
            except Exception as e:
                return e
            finally:
                liq_snapshot_latency_seconds.labels(provider=p.name).observe(
                    asyncio.get_event_loop().time() - start
                )

        results = await asyncio.gather(*(_call(p) for p in self._providers))
        return list(zip(self._providers, results, strict=True))

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
            # M1 default: uniform across active providers.
            n = len(active)
            return {o.provider: 1.0 / n for o in active}

        relevant = {o.provider: stored.get(o.provider, WEIGHT_FLOOR) for o in active}
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
        bucket_size = current_price * BUCKET_PCT

        merged: dict[tuple[float, SideLiteral], dict[str, Any]] = defaultdict(
            lambda: {"weight_sum": 0.0, "breakdown": defaultdict(float)}
        )

        for o in outputs:
            w = weights.get(o.provider, 0.0)
            if w == 0.0:
                continue
            for b in o.buckets:
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
            zones.append(
                MagnetZone(
                    price_low=price_low,
                    price_high=price_high,
                    side=side,
                    est_volume_usd=entry["weight_sum"],
                    distance_pct=distance_pct,
                    source_breakdown=dict(entry["breakdown"]),
                    confidence="medium",  # overridden after agreement is known
                )
            )

        zones.sort(key=lambda z: abs(z.distance_pct))
        return zones

    def _imbalance(self, zones: list[MagnetZone], current_price: float) -> float:
        band_pct = IMBALANCE_BAND_PCT
        long_vol = sum(
            z.est_volume_usd
            for z in zones
            if z.side == "long_liq" and abs(z.distance_pct) <= band_pct
        )
        short_vol = sum(
            z.est_volume_usd
            for z in zones
            if z.side == "short_liq" and abs(z.distance_pct) <= band_pct
        )
        if short_vol == 0 and long_vol == 0:
            return 1.0
        if short_vol == 0:
            return float("inf")
        return long_vol / short_vol

    def _density(self, zones: list[MagnetZone], current_price: float) -> float:
        if not zones:
            return 0.0
        total = sum(z.est_volume_usd for z in zones)
        if total == 0:
            return 0.0
        near = sum(z.est_volume_usd for z in zones if abs(z.distance_pct) <= DENSITY_BAND_PCT)
        return min(1.0, near / total)

    def _agreement(
        self,
        outputs: list[ProviderHeatmap],
        current_price: float,
    ) -> float:
        """1 - average coefficient of variation over top-5 buckets across providers.

        High agreement = providers point at similar zones at similar ranks.
        """
        if len(outputs) < 2:
            return 1.0  # only one provider; trivially "agrees with itself"

        top_5_per_provider: list[list[RawProviderBucket]] = [
            sorted(o.buckets, key=lambda b: -b.est_volume_usd)[:5] for o in outputs
        ]
        valid = [t for t in top_5_per_provider if t]
        if len(valid) < 2:
            return 0.5  # neutral

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
        # Empirically, CV ≤ 0.05 → high agreement; CV ≥ 0.05 → ~0.
        return max(0.0, min(1.0, 1.0 - avg_cv * 20.0))

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
            if (
                z.side == "long_liq"
                and z.distance_pct < 0
                and (nearest_long is None or abs(z.distance_pct) < abs(nearest_long.distance_pct))
            ):
                nearest_long = z
            elif (
                z.side == "short_liq"
                and z.distance_pct > 0
                and (nearest_short is None or abs(z.distance_pct) < abs(nearest_short.distance_pct))
            ):
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
