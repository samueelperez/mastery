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

    # +DI / -DI: preservamos null durante warmup (atr_w es null hasta `length`
    # velas). Tras warmup, si atr_w == 0 (mercado totalmente plano N velas
    # seguidas — extremadamente raro pero posible) devolvemos 0, no inf.
    plus_di_expr = (
        pl.when(atr_w.is_null())
        .then(None)
        .when(atr_w > 0)
        .then(100.0 * plus_dm_w / atr_w)
        .otherwise(0.0)
    )
    minus_di_expr = (
        pl.when(atr_w.is_null())
        .then(None)
        .when(atr_w > 0)
        .then(100.0 * minus_dm_w / atr_w)
        .otherwise(0.0)
    )
    plus_di = plus_di_expr.alias(out_plus_di)
    minus_di = minus_di_expr.alias(out_minus_di)

    # DX = 100·|+DI − -DI| / (+DI + -DI). Cuando ambos DIs son 0 (mercado
    # plano post-warmup), DX colapsa a 0 (no a NaN). `fill_null(0.0)` sigue
    # cubriendo el warmup `min_samples=length` para que la EWM de Wilder
    # arranque limpia. Tras esto, ADX = Wilder(DX).
    di_sum = plus_di_expr + minus_di_expr
    dx = (
        pl.when(di_sum > 0)
        .then(100.0 * (plus_di_expr - minus_di_expr).abs() / di_sum)
        .otherwise(0.0)
        .fill_null(0.0)
    )
    adx_v = dx.ewm_mean(alpha=alpha, adjust=False, min_samples=length).alias(out_adx)

    return df.with_columns(plus_di, minus_di, adx_v)
