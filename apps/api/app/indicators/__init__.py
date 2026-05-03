"""Technical indicators on Polars expressions.

All public functions accept a `pl.LazyFrame` (or DataFrame; auto-converted) with at
least the columns from `app.data.types.OHLCVCandle` (`o`, `h`, `l`, `c`, `v`, `ts`).
They return a LazyFrame extended with one or more new columns; original columns are
preserved. Functions never mutate or remove the input columns.

Closed-candle invariant: callers must filter `is_closed=True` before passing rows
in. Indicators do not check this.
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
