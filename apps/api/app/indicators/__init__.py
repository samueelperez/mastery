"""Technical indicators on Polars expressions.

All public functions accept a `pl.LazyFrame` (or DataFrame; auto-converted) with at
least the columns from `app.data.types.OHLCVCandle` (`o`, `h`, `l`, `c`, `v`, `ts`).
They return a LazyFrame extended with one or more new columns; original columns are
preserved. Functions never mutate or remove the input columns.

Closed-candle invariant: rows passed in must represent fully-closed candles.
Enforced upstream — `app.storage.ohlcv_repo.fetch_range` clamps the upper
bound to `floor_to_timeframe(now, tf)`, and the live ingestor only persists
candles with `kline.is_closed=True`. Indicators trust this invariant and do
not re-check it.
"""

from app.indicators.core import atr, ema, rsi, sma
from app.indicators.momentum import bbands, macd
from app.indicators.panel import IndicatorSpec, compute_panel
from app.indicators.trend import adx
from app.indicators.volume import vwap

__all__ = [
    "IndicatorSpec",
    "adx",
    "atr",
    "bbands",
    "compute_panel",
    "ema",
    "macd",
    "rsi",
    "sma",
    "vwap",
]
