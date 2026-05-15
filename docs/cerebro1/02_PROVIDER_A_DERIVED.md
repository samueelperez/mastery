# 02 — Provider A: Derived from WS Trades

<context>
This provider reconstructs liquidation magnet zones from the trades stream we already capture via `ccxt.pro` in `apps/api/app/market/ohlcv/ingestion_live.py`. It does NOT call any external API. Cost: €0. Coverage: Binance USDM + Bybit USDT perps.

The approach: for each significant trade (>= $100k notional), estimate the liquidation price of the counterparty's position at each of 7 leverage brackets (3×, 5×, 10×, 25×, 50×, 75×, 100×), weight by trade size and leverage distribution prior, decay by trade age, bucket into price bins, and return the top N buckets by aggregated weight.
</context>

<deliverables>
- `apps/api/app/liquidation/providers/base.py` — ABC for all providers.
- `apps/api/app/liquidation/providers/derived.py` — this provider.
- `apps/api/app/liquidation/providers/_leverage.py` — leverage / maintenance margin constants and helpers (shared with other providers).
- `apps/api/tests/liquidation/providers/test_derived.py` — unit tests.
- Persistence: this provider does NOT write to DB directly. It returns `ProviderHeatmap`. The service handles persistence.
</deliverables>

<prerequisite_check>
Before implementing, verify the trades stream exists. Open `apps/api/app/market/ohlcv/ingestion_live.py` and confirm:

- It subscribes to a trades channel (not just klines).
- Trades are persisted to a table or accessible via Valkey pubsub.

**If trades are NOT being captured currently**, this provider needs a prerequisite PR to add trades ingestion. Flag this and pause — do not invent a fake trades source. The market module spec says LiveIngestion subscribes to klines; trades may need to be added.

If trades exist but in Valkey only (not persisted), Provider A operates on a rolling in-memory window. If trades are in a `market_trades` table, Provider A queries it. Pick the implementation path based on what you find. Default assumption: trades need to be added; if so, add them in a small prerequisite PR before this one.
</prerequisite_check>

<file_apps_api_app_liquidation_providers_base_py>

```python
"""Abstract base class for liquidation data providers.

Every concrete provider implements `get_heatmap` and `health_check`. The
service layer iterates over all enabled providers and merges their outputs.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from app.liquidation.models import ProviderHeatmap, ProviderName, TimeframeLiteral


class BaseLiquidationProvider(ABC):
    """Contract for any liquidation data provider."""

    # Set by concrete subclasses. Must match a value of `ProviderName`.
    name: ClassVar[ProviderName]

    # Maximum age (seconds) a snapshot can be before being considered stale.
    # The service excludes stale providers from the merge.
    max_age_seconds: ClassVar[int]

    # Whether this provider is enabled. Used to defer Coinglass without
    # removing code.
    enabled: ClassVar[bool] = True

    @abstractmethod
    async def get_heatmap(
        self,
        symbol: str,
        timeframe: TimeframeLiteral,
        current_price: float,
        max_distance_pct: float = 10.0,
    ) -> ProviderHeatmap:
        """Return raw buckets for this provider's view of the heatmap.

        Args:
            symbol: Internal symbol, e.g. 'BTCUSDT'. Provider is responsible
                for mapping to its own symbol space (e.g. 'BTC' for Hyperliquid).
            timeframe: '1h', '4h', or '1d'. Influences the temporal window
                the provider looks back over.
            current_price: Reference price for distance calculations.
            max_distance_pct: Only return buckets within ±max_distance_pct
                of current_price. Default 10%.

        Returns:
            ProviderHeatmap with buckets list (may be empty) and warnings.

        Must NOT raise on transient errors; instead return empty buckets
        with warnings populated. Only raise on programmer errors
        (invalid symbol, etc).
        """
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Quick sanity check. Return True if the provider can serve data
        right now."""
        ...

    @abstractmethod
    def supports_symbol(self, symbol: str) -> bool:
        """Whether this provider has coverage for the given symbol."""
        ...
```
</file_apps_api_app_liquidation_providers_base_py>

<file_apps_api_app_liquidation_providers_leverage_py>

```python
"""Leverage and maintenance margin constants.

Shared by all providers and tests. Values are based on Hyperliquid's
maintenance margin formula (half of initial margin at max leverage). Binance
and Bybit have similar tiered structures; for the derived provider we use
this approximation since we don't know which exchange a counterparty was on.
"""
from __future__ import annotations

# Leverage brackets used in liquidation estimation.
# Distribution weights are empirical priors: most retail uses 5-25x.
LEVERAGE_BRACKETS: tuple[int, ...] = (3, 5, 10, 25, 50, 75, 100)

LEVERAGE_PRIOR_WEIGHTS: dict[int, float] = {
    3: 0.10,
    5: 0.20,
    10: 0.30,
    25: 0.20,
    50: 0.10,
    75: 0.05,
    100: 0.05,
}

# Maintenance margin as fraction (e.g. 0.005 = 0.5%).
# Computed as ~half of initial margin (1/leverage).
MAINTENANCE_MARGIN: dict[int, float] = {
    3: 0.167,
    5: 0.10,
    10: 0.05,
    25: 0.02,
    50: 0.01,
    75: 0.0067,
    100: 0.005,
}

assert sum(LEVERAGE_PRIOR_WEIGHTS.values()) == 1.0, (
    f"Leverage prior weights must sum to 1.0, got {sum(LEVERAGE_PRIOR_WEIGHTS.values())}"
)


def estimate_liq_price(trade_px: float, side: str, leverage: int) -> float:
    """Estimate the liquidation price of the counterparty's position.

    Args:
        trade_px: Price at which the trade executed.
        side: Aggressor side. 'B' (buy) means the counterparty went short, so
            their liquidation is ABOVE the entry. 'S' (sell) means the
            counterparty went long, so their liquidation is BELOW.
        leverage: Assumed leverage of the counterparty's position.

    Returns:
        Estimated liquidation price.

    Notes:
        This is a heuristic: we don't actually know the counterparty's
        leverage. The service weights this estimate by LEVERAGE_PRIOR_WEIGHTS
        across all brackets.
    """
    if leverage not in MAINTENANCE_MARGIN:
        raise ValueError(f"Unsupported leverage: {leverage}")
    mm = MAINTENANCE_MARGIN[leverage]
    if side == "B":  # counterparty is SHORT, liquidation is ABOVE
        return trade_px * (1 + 1 / leverage - mm)
    elif side == "S":  # counterparty is LONG, liquidation is BELOW
        return trade_px * (1 - 1 / leverage + mm)
    else:
        raise ValueError(f"Invalid side: {side!r}. Expected 'B' or 'S'.")


def opposite_side(trade_side: str) -> str:
    """Map trade aggressor side to the side that gets liquidated.

    A buy aggressor lifts the offer (someone sold short, may liquidate up).
    A sell aggressor hits the bid (someone bought long, may liquidate down).
    """
    if trade_side == "B":
        return "short_liq"
    elif trade_side == "S":
        return "long_liq"
    else:
        raise ValueError(f"Invalid side: {trade_side!r}")
```
</file_apps_api_app_liquidation_providers_leverage_py>

<file_apps_api_app_liquidation_providers_derived_py>

```python
"""Provider A: liquidation heatmap derived from WS trades stream.

Reconstructs liquidation magnet zones by estimating where leveraged
counterparties of each trade would get liquidated, weighted by trade size,
leverage distribution prior, and time decay.

Approach (per-call):
1. Fetch trades >= MIN_TRADE_USD from the temporal window for the symbol.
2. For each trade, for each leverage bracket, estimate liq_price.
3. Weight = trade_size_usd * leverage_prior_weight * time_decay.
4. Bucket prices into bins of `bucket_pct` * current_price.
5. Sum weights per bucket; filter buckets with total > MIN_BUCKET_FRACTION
   of grand total.
6. Return RawProviderBuckets sorted by distance to current_price.

Time decay: weight *= exp(-age_hours / HALF_LIFE_HOURS), with HALF_LIFE_HOURS
configurable per timeframe.

This implementation uses Polars for vectorized aggregation. The expensive
work is the trade fetch; the rest is sub-second on 100k trades.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import ClassVar, Final

import polars as pl
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.liquidation.models import ProviderHeatmap, ProviderName, RawProviderBucket, TimeframeLiteral
from app.liquidation.providers.base import BaseLiquidationProvider
from app.liquidation.providers._leverage import (
    LEVERAGE_BRACKETS,
    LEVERAGE_PRIOR_WEIGHTS,
    estimate_liq_price,
    opposite_side,
)

# Configurable constants. Move to Settings if you need runtime override.
MIN_TRADE_USD: Final[float] = 100_000.0
MIN_BUCKET_FRACTION: Final[float] = 0.01  # 1% of total weight
BUCKET_PCT: Final[float] = 0.0025  # 0.25% of current_price per bucket

# Lookback windows per timeframe (in hours).
LOOKBACK_HOURS: Final[dict[TimeframeLiteral, int]] = {
    "1h": 24 * 7,    # 7 days for 1h heatmap
    "4h": 24 * 30,   # 30 days for 4h heatmap
    "1d": 24 * 90,   # 90 days for 1d heatmap
}

# Half-life of trade influence (hours). Older trades count less.
HALF_LIFE_HOURS: Final[dict[TimeframeLiteral, float]] = {
    "1h": 12.0,
    "4h": 48.0,
    "1d": 168.0,
}

# Symbols this provider covers. Limited to perps with deep WS trade streams.
SUPPORTED_SYMBOLS: Final[frozenset[str]] = frozenset({
    "BTCUSDT", "ETHUSDT", "SOLUSDT",
})


class DerivedLiquidationProvider(BaseLiquidationProvider):
    """Provider A — derived liquidation heatmap from WS trades."""

    name: ClassVar[ProviderName] = "A_derived"
    max_age_seconds: ClassVar[int] = 30  # WS-driven; very fresh
    enabled: ClassVar[bool] = True

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    def supports_symbol(self, symbol: str) -> bool:
        return symbol in SUPPORTED_SYMBOLS

    async def health_check(self) -> bool:
        """Returns True if we have any trade in the last 5 minutes for any
        supported symbol."""
        async with self._session_factory() as session:
            row = await session.execute(text(
                "SELECT COUNT(*) FROM market_trades "
                "WHERE ts > now() - interval '5 minutes'"
            ))
            return (row.scalar_one() or 0) > 0

    async def get_heatmap(
        self,
        symbol: str,
        timeframe: TimeframeLiteral,
        current_price: float,
        max_distance_pct: float = 10.0,
    ) -> ProviderHeatmap:
        if not self.supports_symbol(symbol):
            return ProviderHeatmap(
                provider=self.name,
                symbol=symbol,
                timeframe=timeframe,
                as_of=datetime.now(tz=UTC),
                buckets=[],
                warnings=[f"symbol_not_supported:{symbol}"],
            )

        now = datetime.now(tz=UTC)
        lookback_h = LOOKBACK_HOURS[timeframe]
        since = now - timedelta(hours=lookback_h)
        half_life_h = HALF_LIFE_HOURS[timeframe]

        # 1. Fetch significant trades.
        trades = await self._fetch_trades(symbol, since, MIN_TRADE_USD)
        if trades.is_empty():
            return ProviderHeatmap(
                provider=self.name,
                symbol=symbol,
                timeframe=timeframe,
                as_of=now,
                buckets=[],
                warnings=["no_significant_trades_in_window"],
            )

        # 2-3. Estimate liq prices and weights, vectorized in Polars.
        bucket_size = current_price * BUCKET_PCT
        max_dist_abs = current_price * max_distance_pct / 100.0
        bucket_df = self._compute_buckets(
            trades=trades,
            current_price=current_price,
            bucket_size=bucket_size,
            max_dist_abs=max_dist_abs,
            half_life_h=half_life_h,
            now=now,
        )

        if bucket_df.is_empty():
            return ProviderHeatmap(
                provider=self.name,
                symbol=symbol,
                timeframe=timeframe,
                as_of=now,
                buckets=[],
                warnings=["no_buckets_in_range"],
            )

        # 4. Filter buckets with < MIN_BUCKET_FRACTION of total weight.
        total_weight = bucket_df["weight"].sum() or 1.0
        bucket_df = bucket_df.with_columns(
            (pl.col("weight") / total_weight).alias("fraction")
        ).filter(pl.col("fraction") >= MIN_BUCKET_FRACTION)

        # 5. Convert to RawProviderBucket.
        buckets = [
            RawProviderBucket(
                price_low=float(row["price_low"]),
                price_high=float(row["price_high"]),
                side=row["side"],
                est_volume_usd=float(row["weight"]),
                provider=self.name,
                as_of=now,
            )
            for row in bucket_df.iter_rows(named=True)
        ]

        return ProviderHeatmap(
            provider=self.name,
            symbol=symbol,
            timeframe=timeframe,
            as_of=now,
            buckets=buckets,
            warnings=[],
        )

    # ---------------------------------------------------------------
    # Internals
    # ---------------------------------------------------------------
    async def _fetch_trades(
        self,
        symbol: str,
        since: datetime,
        min_usd: float,
    ) -> pl.DataFrame:
        """Fetch trades with notional >= min_usd from `since` to now.

        Returns columns: ts (datetime), price (float), size (float), side (str).
        """
        query = text("""
            SELECT ts, price, size, side
            FROM market_trades
            WHERE symbol = :symbol
              AND ts >= :since
              AND (price * size) >= :min_usd
            ORDER BY ts ASC
        """)
        async with self._session_factory() as session:
            result = await session.execute(
                query,
                {"symbol": symbol, "since": since, "min_usd": min_usd},
            )
            rows = result.fetchall()
        if not rows:
            return pl.DataFrame(schema={
                "ts": pl.Datetime("us", "UTC"),
                "price": pl.Float64,
                "size": pl.Float64,
                "side": pl.Utf8,
            })
        return pl.DataFrame({
            "ts": [r.ts for r in rows],
            "price": [float(r.price) for r in rows],
            "size": [float(r.size) for r in rows],
            "side": [r.side for r in rows],
        })

    def _compute_buckets(
        self,
        trades: pl.DataFrame,
        current_price: float,
        bucket_size: float,
        max_dist_abs: float,
        half_life_h: float,
        now: datetime,
    ) -> pl.DataFrame:
        """Vectorized computation of bucketed weights.

        For each trade and each leverage bracket, compute:
          liq_px       = f(trade_price, side, leverage)
          weight       = trade_notional * leverage_prior * time_decay
          bucket_low   = floor(liq_px / bucket_size) * bucket_size
          bucket_high  = bucket_low + bucket_size
          liq_side     = opposite of trade aggressor side

        Returns columns: price_low, price_high, side, weight (sum per bucket).
        """
        # Add per-trade notional and age.
        trades = trades.with_columns([
            (pl.col("price") * pl.col("size")).alias("notional"),
            ((now - pl.col("ts")).dt.total_seconds() / 3600.0).alias("age_h"),
        ]).with_columns([
            (-pl.col("age_h") * math.log(2) / half_life_h).exp().alias("time_decay"),
        ])

        # Cross-join with leverage brackets (one row per trade per leverage).
        leverage_df = pl.DataFrame({
            "leverage": list(LEVERAGE_BRACKETS),
            "lev_weight": [LEVERAGE_PRIOR_WEIGHTS[l] for l in LEVERAGE_BRACKETS],
        })
        expanded = trades.join(leverage_df, how="cross")

        # Compute liquidation price per (trade, leverage).
        # Apply the closed-form formula in expressions for speed.
        # Note: we keep MAINTENANCE_MARGIN aligned via a join too.
        from app.liquidation.providers._leverage import MAINTENANCE_MARGIN
        mm_df = pl.DataFrame({
            "leverage": list(LEVERAGE_BRACKETS),
            "mm": [MAINTENANCE_MARGIN[l] for l in LEVERAGE_BRACKETS],
        })
        expanded = expanded.join(mm_df, on="leverage", how="left")

        # liq_px depends on side: side='B' -> +(1/L - mm), side='S' -> -(1/L - mm)
        expanded = expanded.with_columns(
            pl.when(pl.col("side") == "B")
              .then(pl.col("price") * (1 + 1 / pl.col("leverage") - pl.col("mm")))
              .otherwise(pl.col("price") * (1 - 1 / pl.col("leverage") + pl.col("mm")))
              .alias("liq_px"),
            pl.when(pl.col("side") == "B")
              .then(pl.lit("short_liq"))
              .otherwise(pl.lit("long_liq"))
              .alias("liq_side"),
            (pl.col("notional") * pl.col("lev_weight") * pl.col("time_decay")).alias("w"),
        )

        # Filter to within max_distance_pct of current_price.
        expanded = expanded.filter(
            (pl.col("liq_px") - current_price).abs() <= max_dist_abs
        )

        if expanded.is_empty():
            return pl.DataFrame(schema={
                "price_low": pl.Float64,
                "price_high": pl.Float64,
                "side": pl.Utf8,
                "weight": pl.Float64,
            })

        # Bucket by floor(liq_px / bucket_size) * bucket_size.
        expanded = expanded.with_columns(
            ((pl.col("liq_px") / bucket_size).floor() * bucket_size).alias("price_low"),
        ).with_columns(
            (pl.col("price_low") + bucket_size).alias("price_high"),
        )

        return (
            expanded
            .group_by(["price_low", "price_high", "liq_side"])
            .agg(pl.col("w").sum().alias("weight"))
            .rename({"liq_side": "side"})
            .sort("weight", descending=True)
        )
```
</file_apps_api_app_liquidation_providers_derived_py>

<gotchas>
- The `market_trades` table is assumed to exist with columns `ts, symbol, price, size, side` where `side ∈ {'B','S'}`. If your schema differs, adapt the SELECT. If the table doesn't exist, this provider can't ship — add trade capture to LiveIngestion first (small prerequisite PR).
- Polars `.dt.total_seconds()` requires a Duration expression; `(now - pl.col("ts"))` creates a Duration, then `.dt.total_seconds()` converts.
- `pl.col(...).exp()` exists; `pl.col(...).log()` too. Use them, not `math.exp` on columns.
- The cross-join + per-bracket computation can balloon memory: 100k trades × 7 brackets = 700k rows. Polars handles this fine, but if you're testing with millions of trades, lower `MIN_TRADE_USD` threshold tuning.
- Don't forget the time decay sign: `exp(-age * ln2 / half_life)`, not `exp(-age / half_life)`.
- `now` must be passed in, not computed inside `_compute_buckets`, otherwise tests can't pin time.
- `RawProviderBucket.est_volume_usd` here is `weight`, not real USD. That's intentional — it's a relative weight that the service normalizes. Document this in the docstring.
</gotchas>

<no_lookahead_invariant>
This provider MUST be no-lookahead. Verify by:

1. Run `get_heatmap` at time T.
2. Add a synthetic trade with `ts > T`.
3. Run `get_heatmap` at time T again.
4. Result must be identical bit-for-bit.

This is testable. See `07_TESTING.md::test_provider_a_no_lookahead`.

The implementation respects this because:
- `_fetch_trades` filters `ts >= since` and `ts <= now`. If `now` is fixed (passed in), future trades are excluded.
- Time decay uses `now - ts`, also fixed.

If you refactor, preserve this invariant.
</no_lookahead_invariant>

<acceptance>
- [ ] `apps/api/app/liquidation/providers/base.py` exists with the ABC.
- [ ] `apps/api/app/liquidation/providers/_leverage.py` exists with constants + 2 helpers.
- [ ] `apps/api/app/liquidation/providers/derived.py` implements `DerivedLiquidationProvider`.
- [ ] `python -c "from app.liquidation.providers.derived import DerivedLiquidationProvider"` succeeds.
- [ ] Unit tests in `tests/liquidation/providers/test_derived.py` pass (see `07_TESTING.md`).
- [ ] No-lookahead test passes.
- [ ] `health_check` returns False when DB has no recent trades.
- [ ] `get_heatmap` returns empty buckets (not raises) when there are no trades.
</acceptance>
