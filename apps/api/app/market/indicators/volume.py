"""Volume indicators: VWAP (session-anchored)."""

from __future__ import annotations

from typing import Literal

import polars as pl

Anchor = Literal["session", "week", "none"]


def vwap(
    df: pl.LazyFrame,
    *,
    anchor: Anchor = "session",
    out: str = "vwap",
) -> pl.LazyFrame:
    """Volume-weighted average price.

    Uses the typical price `(h+l+c)/3` weighted by volume:
        vwap = cumsum(typical_price * v) / cumsum(v)

    Anchor determines where the cumulative sums reset:
      - "session" → reset at each UTC day boundary (00:00 UTC).
      - "week"    → reset at each ISO week boundary (Monday 00:00 UTC).
      - "none"    → cumulative since the start of the input (rolling all-time).

    Requires a `ts` column (TIMESTAMPTZ). Rows are assumed sorted ascending by ts.
    """
    typical = (pl.col("h") + pl.col("l") + pl.col("c")) / 3.0
    weighted = typical * pl.col("v")

    if anchor == "none":
        cum_pv = weighted.cum_sum()
        cum_v = pl.col("v").cum_sum()
        return df.with_columns((cum_pv / cum_v).alias(out))

    bucket = (
        pl.col("ts").dt.truncate("1d") if anchor == "session"
        else pl.col("ts").dt.truncate("1w")
    )
    return df.with_columns(
        (weighted.cum_sum().over(bucket) / pl.col("v").cum_sum().over(bucket)).alias(out)
    )
