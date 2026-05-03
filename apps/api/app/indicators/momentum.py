"""Momentum indicators: MACD, Bollinger Bands."""

from __future__ import annotations

import polars as pl


def macd(
    df: pl.LazyFrame,
    *,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
    source: str = "c",
    out_macd: str = "macd",
    out_signal: str = "macd_signal",
    out_hist: str = "macd_hist",
) -> pl.LazyFrame:
    """MACD = EMA(fast) - EMA(slow); signal = EMA(MACD, signal); hist = MACD - signal.

    Uses `adjust=False` (the trader-facing convention; matches TradingView).
    """
    src = pl.col(source)
    ema_fast = src.ewm_mean(span=fast, adjust=False, min_samples=fast)
    ema_slow = src.ewm_mean(span=slow, adjust=False, min_samples=slow)
    macd_line = (ema_fast - ema_slow).alias(out_macd)
    signal_line = (
        macd_line.ewm_mean(span=signal, adjust=False, min_samples=signal).alias(out_signal)
    )
    hist = (macd_line - signal_line).alias(out_hist)
    return df.with_columns(macd_line, signal_line, hist)


def bbands(
    df: pl.LazyFrame,
    *,
    length: int = 20,
    stds: float = 2.0,
    source: str = "c",
    out_mid: str = "bb_mid",
    out_upper: str = "bb_upper",
    out_lower: str = "bb_lower",
    out_bw: str = "bb_bw",
) -> pl.LazyFrame:
    """Bollinger Bands.

    `bw` (bandwidth) is the normalised band width (`(upper-lower) / mid`), useful
    for squeeze detection without re-computing.
    """
    src = pl.col(source)
    mid = src.rolling_mean(window_size=length, min_samples=length).alias(out_mid)
    std = src.rolling_std(window_size=length, min_samples=length, ddof=0)
    upper = (mid + std * stds).alias(out_upper)
    lower = (mid - std * stds).alias(out_lower)
    bw = ((upper - lower) / mid).alias(out_bw)
    return df.with_columns(mid, upper, lower, bw)
