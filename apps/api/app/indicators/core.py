"""Core indicators: SMA, EMA, RSI, ATR.

All functions are Polars-native: they fold into a single `with_columns` call so the
caller can chain them efficiently (one `collect()` for a panel of indicators).
"""

from __future__ import annotations

import polars as pl


def sma(
    df: pl.LazyFrame,
    *,
    length: int,
    source: str = "c",
    out: str | None = None,
) -> pl.LazyFrame:
    """Simple moving average.

    `min_samples=length` so the first `length-1` rows are null (no leading-edge
    artefacts that look like real values).
    """
    col = out or f"sma_{length}"
    return df.with_columns(
        pl.col(source).rolling_mean(window_size=length, min_samples=length).alias(col)
    )


def ema(
    df: pl.LazyFrame,
    *,
    length: int,
    source: str = "c",
    out: str | None = None,
) -> pl.LazyFrame:
    """Exponential moving average using the standard `adjust=False` recursion.

    Matches every common reference implementation (TA-Lib, pandas-ta default,
    TradingView's EMA): seed = first value, then EMA[i] = α·X[i] + (1-α)·EMA[i-1].
    """
    col = out or f"ema_{length}"
    return df.with_columns(
        pl.col(source).ewm_mean(span=length, adjust=False, min_samples=length).alias(col)
    )


def rsi(
    df: pl.LazyFrame,
    *,
    length: int = 14,
    source: str = "c",
    out: str | None = None,
) -> pl.LazyFrame:
    """Wilder's RSI.

    Wilder smoothing is mathematically equivalent to an EMA with α=1/length, which
    in Polars maps to `ewm_mean(alpha=1/length, adjust=False)`. We use the explicit
    alpha form to make the equivalence obvious to readers.
    """
    col = out or f"rsi_{length}"
    diff = pl.col(source).diff()
    gain = pl.when(diff > 0).then(diff).otherwise(0.0)
    loss = pl.when(diff < 0).then(-diff).otherwise(0.0)
    avg_gain = gain.ewm_mean(alpha=1 / length, adjust=False, min_samples=length)
    avg_loss = loss.ewm_mean(alpha=1 / length, adjust=False, min_samples=length)
    rs = avg_gain / avg_loss
    return df.with_columns((100.0 - 100.0 / (1.0 + rs)).alias(col))


def atr(
    df: pl.LazyFrame,
    *,
    length: int = 14,
    out: str | None = None,
) -> pl.LazyFrame:
    """Wilder's Average True Range.

    True Range = max(h-l, |h - c[t-1]|, |l - c[t-1]|). For the first row,
    h-c[t-1] and l-c[t-1] are null, so TR collapses to h-l. ATR is then the
    Wilder-smoothed mean (α=1/length).
    """
    col = out or f"atr_{length}"
    prev_close = pl.col("c").shift(1)
    tr = pl.max_horizontal(
        pl.col("h") - pl.col("l"),
        (pl.col("h") - prev_close).abs(),
        (pl.col("l") - prev_close).abs(),
    )
    return df.with_columns(
        tr.ewm_mean(alpha=1 / length, adjust=False, min_samples=length).alias(col)
    )
