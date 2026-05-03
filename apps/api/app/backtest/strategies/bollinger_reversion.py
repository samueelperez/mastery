"""Bollinger-band mean reversion.

Entry: close pierces below `bb_lower`.
Exit:  close crosses back above `bb_mid` OR a hard stop at `bb_lower - k·ATR`
       (so a real breakdown doesn't trap the position).
"""

from __future__ import annotations

from typing import Any

import polars as pl

from app.backtest.strategies import SignalFrame, register
from app.indicators.core import atr
from app.indicators.momentum import bbands


@register(
    id="bb_reversion_atr_stop",
    description=(
        "Long mean-reversion: enter when close < bb_lower; exit when close > bb_mid "
        "or price drops below bb_lower - k·ATR (hard stop on real breakdown)."
    ),
    default_params={"length": 20, "stds": 2.0, "atr_length": 14, "atr_k": 1.0},
)
def bb_reversion_atr_stop(df: pl.DataFrame, params: dict[str, Any]) -> SignalFrame:
    length = int(params.get("length", 20))
    stds = float(params.get("stds", 2.0))
    atr_len = int(params.get("atr_length", 14))
    atr_k = float(params.get("atr_k", 1.0))

    lf = df.lazy()
    lf = bbands(lf, length=length, stds=stds, source="c")
    lf = atr(lf, length=atr_len, out=f"atr_{atr_len}")
    enriched = lf.collect()

    close = enriched["c"]
    bb_lower = enriched["bb_lower"]
    bb_mid = enriched["bb_mid"]

    # entry: close < lower band, but only on the bar where it crosses below
    below_lower = close < bb_lower
    below_lower_prev = below_lower.shift(1).fill_null(value=False)
    entry = below_lower & ~below_lower_prev

    # exit: close > mid band (mean reverted)
    above_mid = close > bb_mid
    above_mid_prev = above_mid.shift(1).fill_null(value=False)
    exit_ = above_mid & ~above_mid_prev

    # hard stop distance from entry: bb_lower - k·ATR (negative, so we go below)
    stop_distance = enriched[f"atr_{atr_len}"] * atr_k

    return SignalFrame(df=enriched, entry=entry, exit_=exit_, stop_distance=stop_distance)
