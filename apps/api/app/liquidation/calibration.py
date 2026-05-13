"""Calibration job: compute provider weights from agreement log.

Runs weekly from M2 onward (cron task or manual trigger). Output goes to
`liquidation_provider_weights`; the service reads this table at snapshot
time to weight provider contributions.

In M1 this module exists but isn't invoked — the service falls back to
uniform weights when the table is empty.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from app.liquidation.models import ProviderName, ProviderWeight, TimeframeLiteral
from app.liquidation.repo import LiquidationRepo

# Floor weight: no provider falls below 10% even if agreement is 0.
# Mirrors the DB CHECK constraint `weight >= 0.10`.
WEIGHT_FLOOR: float = 0.10

# Rolling window of agreement_log entries to consider.
ROLLING_WINDOW_DAYS: int = 30

# Minimum samples per (symbol, tf, provider) before computing a weight.
MIN_SAMPLES: int = 10

# Max delta (%) at which a provider is considered "close to" the proposed zone.
PROVIDER_AGREE_THRESHOLD_PCT: float = 0.5


async def compute_provider_weights(
    repo: LiquidationRepo,
    *,
    now: datetime | None = None,
) -> list[ProviderWeight]:
    """Compute weights from the operator's TradingDifferent verdicts.

    For each (symbol, timeframe, provider):
      1. Read agreement log rows where verdict ∈ {'agree','close','disagree'}.
      2. Provider sample is True iff TD agreed AND
         |delta_<provider>_pct| ≤ PROVIDER_AGREE_THRESHOLD_PCT.
      3. raw_rate = sum(samples) / len(samples).
      4. weight = max(WEIGHT_FLOOR, raw_rate); normalize per (symbol, tf) so
         weights sum to 1.0.
    """
    now = now or datetime.now(tz=UTC)
    since = now - timedelta(days=ROLLING_WINDOW_DAYS)
    rows = await repo.fetch_agreement_log(since=since)

    by_cell: dict[tuple[str, TimeframeLiteral], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["source_c_verdict"] == "skipped":
            continue
        by_cell[(r["symbol"], r["timeframe"])].append(r)

    out: list[ProviderWeight] = []
    for (symbol, tf), entries in by_cell.items():
        per_provider: dict[ProviderName, list[bool]] = defaultdict(list)
        for e in entries:
            td_agrees = e["source_c_verdict"] in ("agree", "close")
            for provider, delta_key in (
                ("A_derived", "delta_a_pct"),
                ("B_hyperliquid", "delta_b_pct"),
            ):
                d = e.get(delta_key)
                if d is None:
                    continue
                provider_agrees = abs(float(d)) <= PROVIDER_AGREE_THRESHOLD_PCT
                per_provider[provider].append(td_agrees and provider_agrees)

        raw: dict[ProviderName, tuple[float, int]] = {}
        for prov, samples in per_provider.items():
            if len(samples) < MIN_SAMPLES:
                continue
            raw[prov] = (sum(samples) / len(samples), len(samples))

        if not raw:
            continue

        floored = {prov: max(WEIGHT_FLOOR, rate) for prov, (rate, _) in raw.items()}
        total = sum(floored.values()) or 1.0
        normalized = {prov: w / total for prov, w in floored.items()}

        for prov, weight in normalized.items():
            rate, n = raw[prov]
            out.append(
                ProviderWeight(
                    symbol=symbol,
                    timeframe=tf,
                    provider=prov,
                    weight=weight,
                    agreement_rate=rate,
                    n_samples=n,
                    computed_at=now,
                )
            )

    return out
