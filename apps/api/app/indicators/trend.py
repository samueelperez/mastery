"""Trend indicators: ADX (Average Directional Index)."""

from __future__ import annotations

import polars as pl


def adx(
    df: pl.LazyFrame,
    *,
    length: int = 14,
    out_adx: str = "adx",
    out_plus_di: str = "plus_di",
    out_minus_di: str = "minus_di",
) -> pl.LazyFrame:
    """Wilder's ADX with +DI and -DI.

    Steps (textbook Wilder 1978):
      1. up_move   = h[t] - h[t-1]
         down_move = l[t-1] - l[t]
      2. +DM = up_move   if up_move > down_move and up_move   > 0 else 0
         -DM = down_move if down_move > up_move and down_move > 0 else 0
      3. TR = max(h-l, |h - c[t-1]|, |l - c[t-1]|)
      4. Smooth (Wilder = EMA with α=1/length): atr_w, plus_dm_w, minus_dm_w
      5. +DI = 100 · plus_dm_w  / atr_w
         -DI = 100 · minus_dm_w / atr_w
      6. DX  = 100 · |+DI - -DI| / (+DI + -DI)
         ADX = Wilder-smoothed DX
    """
    h, l_, c = pl.col("h"), pl.col("l"), pl.col("c")

    up = h - h.shift(1)
    down = l_.shift(1) - l_

    plus_dm = pl.when((up > down) & (up > 0)).then(up).otherwise(0.0)
    minus_dm = pl.when((down > up) & (down > 0)).then(down).otherwise(0.0)

    prev_c = c.shift(1)
    tr = pl.max_horizontal(h - l_, (h - prev_c).abs(), (l_ - prev_c).abs())

    alpha = 1 / length
    atr_w = tr.ewm_mean(alpha=alpha, adjust=False, min_samples=length)
    plus_dm_w = plus_dm.ewm_mean(alpha=alpha, adjust=False, min_samples=length)
    minus_dm_w = minus_dm.ewm_mean(alpha=alpha, adjust=False, min_samples=length)

    plus_di = (100.0 * plus_dm_w / atr_w).alias(out_plus_di)
    minus_di = (100.0 * minus_dm_w / atr_w).alias(out_minus_di)

    # DX is null while DI is null (warmup). Fill with 0 before the EWM so Polars'
    # `min_samples=length` semantics line up with the textbook Wilder recursion
    # (which seeds DX as 0 during warmup). The leading values are then dominated
    # by zeros and become meaningful only after ~2·length rows — matching ADX's
    # canonical "warmup ≈ 2·length" rule of thumb.
    dx = (100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di)).fill_null(0.0)
    adx_v = dx.ewm_mean(alpha=alpha, adjust=False, min_samples=length).alias(out_adx)

    return df.with_columns(plus_di, minus_di, adx_v)
