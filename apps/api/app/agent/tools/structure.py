"""get_market_structure tool — pivots, S/R, and HH-HL-LH-LL trend label.

Pure Polars; no external dependencies. Pivots are fractal: a swing high at
index i requires h[i] > h[i-k] AND h[i] > h[i+k] for k in 1..pivot_strength.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

import polars as pl
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from app.agent.deps import AgentDeps
from app.agent.tools._envelope import Provenance, ToolResult
from app.agent.tools._time import floor_to_timeframe, staleness_warning
from app.indicators.core import atr
from app.storage.ohlcv_repo import fetch_range


class Pivot(BaseModel):
    ts: datetime
    price: float
    kind: Literal["high", "low"]


class Level(BaseModel):
    price: float
    touches: int = Field(..., ge=1, description="Number of pivots clustered into this level.")
    last_touch_ts: datetime


class MarketStructure(BaseModel):
    swing_highs: list[Pivot] = Field(default_factory=list)
    swing_lows: list[Pivot] = Field(default_factory=list)
    support: list[Level] = Field(default_factory=list)
    resistance: list[Level] = Field(default_factory=list)
    trend_label: Literal["HH_HL", "LH_LL", "mixed", "indeterminate"]
    current_close: float | None = None
    atr_used: float | None = Field(
        default=None, description="ATR(14) used to cluster pivots into levels."
    )


def _find_pivots(
    df: pl.DataFrame, strength: int
) -> tuple[list[Pivot], list[Pivot]]:
    h = df["h"].to_list()
    low = df["l"].to_list()
    ts = df["ts"].to_list()
    n = len(h)
    highs: list[Pivot] = []
    lows: list[Pivot] = []
    for i in range(strength, n - strength):
        is_high = all(h[i] > h[i - k] and h[i] > h[i + k] for k in range(1, strength + 1))
        is_low = all(low[i] < low[i - k] and low[i] < low[i + k] for k in range(1, strength + 1))
        if is_high:
            highs.append(Pivot(ts=ts[i], price=float(h[i]), kind="high"))
        if is_low:
            lows.append(Pivot(ts=ts[i], price=float(low[i]), kind="low"))
    return highs, lows


def _cluster_levels(pivots: list[Pivot], tolerance: float) -> list[Level]:
    """Cluster pivots whose prices are within `tolerance` of each other."""
    if not pivots:
        return []
    by_price = sorted(pivots, key=lambda p: p.price)
    groups: list[list[Pivot]] = [[by_price[0]]]
    for p in by_price[1:]:
        if abs(p.price - groups[-1][-1].price) <= tolerance:
            groups[-1].append(p)
        else:
            groups.append([p])
    return [
        Level(
            price=sum(g_.price for g_ in g) / len(g),
            touches=len(g),
            last_touch_ts=max(g_.ts for g_ in g),
        )
        for g in groups
    ]


def _trend_label(highs: list[Pivot], lows: list[Pivot]) -> str:
    """Last 2 highs and last 2 lows determine HH/HL/LH/LL.

    Returns "HH_HL" (uptrend), "LH_LL" (downtrend), "mixed", or "indeterminate".
    """
    if len(highs) < 2 or len(lows) < 2:
        return "indeterminate"
    h2 = highs[-2:]
    l2 = lows[-2:]
    higher_high = h2[1].price > h2[0].price
    higher_low = l2[1].price > l2[0].price
    lower_high = h2[1].price < h2[0].price
    lower_low = l2[1].price < l2[0].price
    if higher_high and higher_low:
        return "HH_HL"
    if lower_high and lower_low:
        return "LH_LL"
    return "mixed"


def register_structure_tools(agent: Agent[AgentDeps, object]) -> None:
    @agent.tool
    async def get_market_structure(
        ctx: RunContext[AgentDeps],
        symbol: str,
        timeframe: Literal["15m", "1h", "4h", "1d"],
        pivot_strength: Annotated[int, Field(ge=2, le=8)] = 3,
        lookback: Annotated[int, Field(ge=100, le=1500)] = 500,
    ) -> ToolResult[MarketStructure]:
        """Find swing highs/lows (fractal of strength N), cluster into S/R levels
        (tolerance = 0.25·ATR(14)), and label the trend from the last 2 pivots
        of each kind.

        Use to anchor entry/invalidation/target prices on logical levels.
        """
        symbol = symbol.upper()
        cutoff = floor_to_timeframe(datetime.now(tz=UTC), timeframe)
        async with ctx.deps.session_factory() as session:
            rows = await fetch_range(
                session,
                exchange=ctx.deps.exchange,
                symbol=symbol,
                timeframe=timeframe,
                until=cutoff,
                limit=lookback,
            )
        if not rows:
            return ToolResult(
                data=MarketStructure(trend_label="indeterminate"),
                provenance=Provenance(
                    source=f"db.ohlcv:{ctx.deps.exchange}:{symbol}:{timeframe}",
                    as_of=cutoff,
                    rows=0,
                    warnings=["no candles in lookback window"],
                ),
            )
        df = pl.DataFrame(
            {
                "ts": [r.ts for r in rows],
                "o": [r.o for r in rows],
                "h": [r.h for r in rows],
                "l": [r.l for r in rows],
                "c": [r.c for r in rows],
                "v": [r.v for r in rows],
            }
        )

        # ATR(14) on the full panel for clustering tolerance
        atr_df = atr(df.lazy(), length=14).collect()
        atr_last = atr_df["atr_14"].drop_nulls().to_list()
        atr_v = float(atr_last[-1]) if atr_last else None
        tolerance = 0.25 * (atr_v or 0.0)

        highs, lows = _find_pivots(df, pivot_strength)
        support = _cluster_levels(lows, tolerance) if tolerance > 0 else []
        resistance = _cluster_levels(highs, tolerance) if tolerance > 0 else []

        last_close = float(df["c"][-1]) if df.height else None
        last_ts = rows[-1].ts

        warnings = []
        if w := staleness_warning(last_closed=last_ts, timeframe=timeframe):
            warnings.append(w)

        ctx.deps.log.info(
            "tool.get_market_structure",
            symbol=symbol,
            timeframe=timeframe,
            n_highs=len(highs),
            n_lows=len(lows),
        )

        return ToolResult(
            data=MarketStructure(
                swing_highs=highs[-10:],
                swing_lows=lows[-10:],
                support=support[-8:],
                resistance=resistance[-8:],
                trend_label=_trend_label(highs, lows),
                current_close=last_close,
                atr_used=atr_v,
            ),
            provenance=Provenance(
                source=f"db.ohlcv:{ctx.deps.exchange}:{symbol}:{timeframe}",
                as_of=last_ts,
                rows=df.height,
                warnings=warnings,
            ),
        )
