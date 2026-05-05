"""EMA crossover with optional ATR-based stop.

Entry: EMA(fast) crosses above EMA(slow) (long-only here; F2 doesn't model shorts).
Exit:  EMA(fast) crosses below EMA(slow) OR price <= entry_px - k·ATR.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from app.backtest.strategies import SignalFrame, register
from app.indicators.core import atr, ema


@register(
    id="ema_cross_atr_stop",
    name="Cruce de EMAs",
    description=(
        "Estrategia tendencial. Entra largo cuando la EMA rápida cruza por "
        "encima de la lenta; sale en el cruce contrario o si el precio cae "
        "por debajo del entry menos k×ATR (stop adaptativo a volatilidad)."
    ),
    default_params={"fast": 21, "slow": 55, "atr_length": 14, "atr_k": 2.0},
)
def ema_cross_atr_stop(df: pl.DataFrame, params: dict[str, Any]) -> SignalFrame:
    fast = int(params.get("fast", 21))
    slow = int(params.get("slow", 55))
    atr_len = int(params.get("atr_length", 14))
    atr_k = float(params.get("atr_k", 2.0))

    lf = df.lazy()
    lf = ema(lf, length=fast, source="c", out=f"ema_{fast}")
    lf = ema(lf, length=slow, source="c", out=f"ema_{slow}")
    lf = atr(lf, length=atr_len, out=f"atr_{atr_len}")
    enriched = lf.collect()

    fast_col = enriched[f"ema_{fast}"]
    slow_col = enriched[f"ema_{slow}"]

    # crossover: fast > slow AND previous fast <= previous slow
    above = fast_col > slow_col
    above_prev = above.shift(1).fill_null(value=False)
    entry = above & ~above_prev

    # crossunder for the regular exit
    below = fast_col < slow_col
    below_prev = below.shift(1).fill_null(value=False)
    exit_ = below & ~below_prev

    stop_distance = enriched[f"atr_{atr_len}"] * atr_k

    return SignalFrame(df=enriched, entry=entry, exit_=exit_, stop_distance=stop_distance)
