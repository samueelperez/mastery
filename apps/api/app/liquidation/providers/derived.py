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

Time decay: weight *= exp(-age_hours * ln2 / HALF_LIFE_HOURS), with
HALF_LIFE_HOURS configurable per timeframe.

This implementation uses Polars for vectorized aggregation. The expensive
work is the trade fetch; the rest is sub-second on 100k trades.

`RawProviderBucket.est_volume_usd` here is `weight` (relative magnitude),
not real USD — the service normalizes per-source weights before merging.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import ClassVar, Final

import polars as pl
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.liquidation.models import (
    ProviderHeatmap,
    ProviderName,
    RawProviderBucket,
    TimeframeLiteral,
)
from app.liquidation.providers._leverage import (
    LEVERAGE_BRACKETS,
    LEVERAGE_PRIOR_WEIGHTS,
    MAINTENANCE_MARGIN,
)
from app.liquidation.providers.base import BaseLiquidationProvider

# Configurable constants. Move to Settings if you need runtime override.
MIN_TRADE_USD: Final[float] = 100_000.0
MIN_BUCKET_FRACTION: Final[float] = 0.01  # 1% of total weight
BUCKET_PCT: Final[float] = 0.0025  # 0.25% of current_price per bucket

# Lookback windows per timeframe (in hours).
LOOKBACK_HOURS: Final[dict[TimeframeLiteral, int]] = {
    "1h": 24 * 7,  # 7 days for 1h heatmap
    "4h": 24 * 30,  # 30 days for 4h heatmap
    "1d": 24 * 90,  # 90 days for 1d heatmap
}

# Half-life of trade influence (hours). Older trades count less.
HALF_LIFE_HOURS: Final[dict[TimeframeLiteral, float]] = {
    "1h": 12.0,
    "4h": 48.0,
    "1d": 168.0,
}

# Symbols this provider covers. Limited to perps with deep WS trade streams.
SUPPORTED_SYMBOLS: Final[frozenset[str]] = frozenset(
    {
        "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT",
    }
)


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
        """True if we have any trade in the last 5 minutes for any symbol."""
        async with self._session_factory() as session:
            row = await session.execute(
                text("SELECT COUNT(*) FROM market_trades WHERE ts > now() - interval '5 minutes'")
            )
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
        trades = await self._fetch_trades(symbol, since, now, MIN_TRADE_USD)
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
        until: datetime,
        min_usd: float,
    ) -> pl.DataFrame:
        """Fetch trades with notional >= min_usd from `since` to `until`.

        `until` is passed explicitly (not `now()` from SQL) so callers can pin
        time for no-lookahead testing.

        Returns columns: ts (datetime), price (float), size (float), side (str).
        """
        query = text(
            """
            SELECT ts, price, size, side
            FROM market_trades
            WHERE symbol = :symbol
              AND ts >= :since
              AND ts <= :until
              AND (price * size) >= :min_usd
            ORDER BY ts ASC
            """
        )
        async with self._session_factory() as session:
            result = await session.execute(
                query,
                {
                    "symbol": symbol,
                    "since": since,
                    "until": until,
                    "min_usd": min_usd,
                },
            )
            rows = result.fetchall()
        if not rows:
            return pl.DataFrame(
                schema={
                    "ts": pl.Datetime("us", "UTC"),
                    "price": pl.Float64,
                    "size": pl.Float64,
                    "side": pl.Utf8,
                }
            )
        return pl.DataFrame(
            {
                "ts": [r.ts for r in rows],
                "price": [float(r.price) for r in rows],
                "size": [float(r.size) for r in rows],
                "side": [r.side for r in rows],
            }
        )

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

        Pure function on the input DataFrame + `now`. No-lookahead test
        (`test_no_lookahead`) relies on this purity.
        """
        if trades.is_empty():
            return pl.DataFrame(
                schema={
                    "price_low": pl.Float64,
                    "price_high": pl.Float64,
                    "side": pl.Utf8,
                    "weight": pl.Float64,
                }
            )

        trades = trades.with_columns(
            [
                (pl.col("price") * pl.col("size")).alias("notional"),
                ((now - pl.col("ts")).dt.total_seconds() / 3600.0).alias("age_h"),
            ]
        ).with_columns(
            [
                (-pl.col("age_h") * math.log(2) / half_life_h).exp().alias("time_decay"),
            ]
        )

        leverage_df = pl.DataFrame(
            {
                "leverage": list(LEVERAGE_BRACKETS),
                "lev_weight": [LEVERAGE_PRIOR_WEIGHTS[lv] for lv in LEVERAGE_BRACKETS],
                "mm": [MAINTENANCE_MARGIN[lv] for lv in LEVERAGE_BRACKETS],
            }
        )
        expanded = trades.join(leverage_df, how="cross")

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

        expanded = expanded.filter((pl.col("liq_px") - current_price).abs() <= max_dist_abs)

        if expanded.is_empty():
            return pl.DataFrame(
                schema={
                    "price_low": pl.Float64,
                    "price_high": pl.Float64,
                    "side": pl.Utf8,
                    "weight": pl.Float64,
                }
            )

        expanded = expanded.with_columns(
            ((pl.col("liq_px") / bucket_size).floor() * bucket_size).alias("price_low"),
        ).with_columns(
            (pl.col("price_low") + bucket_size).alias("price_high"),
        )

        return (
            expanded.group_by(["price_low", "price_high", "liq_side"])
            .agg(pl.col("w").sum().alias("weight"))
            .rename({"liq_side": "side"})
            .sort("weight", descending=True)
        )
