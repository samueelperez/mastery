"""get_multi_tf_confluence — per-TF bias from EMA stack + structure trend label."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.deps import AgentDeps
from app.agent.tools._envelope import Provenance, ToolResult
from app.agent.tools._time import floor_to_timeframe, staleness_warning
from app.indicators import IndicatorSpec, compute_panel

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class TimeframeBias(BaseModel):
    timeframe: Literal["15m", "1h", "4h", "1d"]
    bias: Literal["bull", "bear", "range"]
    score: int = Field(..., ge=-3, le=3, description="EMA stack score: +1 per condition.")
    reasons: list[str] = Field(default_factory=list)
    last_close: float | None = None
    ema_21: float | None = None
    ema_55: float | None = None
    ema_200: float | None = None


class ConfluenceMap(BaseModel):
    by_tf: list[TimeframeBias]
    aggregate_bias: Literal["bull", "bear", "range"]
    aggregate_agreement_pct: float = Field(..., ge=0.0, le=100.0)


async def _bias_for_tf(
    *,
    session_factory: SessionFactory,
    exchange: str,
    symbol: str,
    tf: str,
) -> tuple[TimeframeBias, datetime, list[str]]:
    cutoff = floor_to_timeframe(datetime.now(tz=UTC), tf)
    async with session_factory() as session:
        df = await compute_panel(
            session,
            exchange=exchange,
            symbol=symbol,
            timeframe=tf,
            lookback=300,
            specs=[
                IndicatorSpec(name="ema", length=21),
                IndicatorSpec(name="ema", length=55),
                IndicatorSpec(name="ema", length=200),
            ],
            until=cutoff,
        )
    if df.height == 0:
        return (
            TimeframeBias(timeframe=tf, bias="range", score=0, reasons=["no candles"]),
            cutoff,
            ["no candles in lookback window"],
        )
    last = df.tail(1).to_dicts()[0]
    close = last["c"]
    ema21 = last.get("ema_21")
    ema55 = last.get("ema_55")
    ema200 = last.get("ema_200")
    score = 0
    reasons: list[str] = []
    if ema21 is not None and ema55 is not None:
        if ema21 > ema55:
            score += 1
            reasons.append("EMA21 > EMA55")
        elif ema21 < ema55:
            score -= 1
            reasons.append("EMA21 < EMA55")
    if ema55 is not None and ema200 is not None:
        if ema55 > ema200:
            score += 1
            reasons.append("EMA55 > EMA200")
        elif ema55 < ema200:
            score -= 1
            reasons.append("EMA55 < EMA200")
    if ema21 is not None:
        if close > ema21:
            score += 1
            reasons.append("close > EMA21")
        elif close < ema21:
            score -= 1
            reasons.append("close < EMA21")
    bias: Literal["bull", "bear", "range"] = (
        "bull" if score >= 2 else "bear" if score <= -2 else "range"
    )
    last_ts = last["ts"]
    warnings: list[str] = []
    if w := staleness_warning(last_closed=last_ts, timeframe=tf):
        warnings.append(w)
    return (
        TimeframeBias(
            timeframe=tf,
            bias=bias,
            score=score,
            reasons=reasons,
            last_close=float(close),
            ema_21=float(ema21) if ema21 is not None else None,
            ema_55=float(ema55) if ema55 is not None else None,
            ema_200=float(ema200) if ema200 is not None else None,
        ),
        last_ts,
        warnings,
    )


def register_confluence_tools(agent: Agent[AgentDeps, object]) -> None:
    @agent.tool
    async def get_multi_tf_confluence(
        ctx: RunContext[AgentDeps],
        symbol: str,
        timeframes: Annotated[
            list[Literal["15m", "1h", "4h", "1d"]] | None,
            Field(min_length=1, max_length=4),
        ] = None,
    ) -> ToolResult[ConfluenceMap]:
        """For each timeframe, compute the bias from the EMA21/55/200 stack
        relative to close. Bias = bull if score≥+2, bear if score≤-2, else range.

        Aggregate bias is the majority across TFs (or "range" on tie).
        """
        symbol = symbol.upper()
        if timeframes is None:
            timeframes = ["15m", "1h", "4h", "1d"]
        results = await asyncio.gather(
            *[
                _bias_for_tf(
                    session_factory=ctx.deps.session_factory,
                    exchange=ctx.deps.exchange,
                    symbol=symbol,
                    tf=tf,
                )
                for tf in timeframes
            ]
        )
        biases = [r[0] for r in results]
        last_ts = max(r[1] for r in results)
        all_warnings = [w for r in results for w in r[2]]

        bull = sum(1 for b in biases if b.bias == "bull")
        bear = sum(1 for b in biases if b.bias == "bear")
        if bull > bear:
            agg = "bull"
            agreement = 100.0 * bull / len(biases)
        elif bear > bull:
            agg = "bear"
            agreement = 100.0 * bear / len(biases)
        else:
            agg = "range"
            agreement = 100.0 * (len(biases) - bull - bear) / len(biases)

        ctx.deps.log.info(
            "tool.get_multi_tf_confluence",
            symbol=symbol,
            n_timeframes=len(timeframes),
            aggregate=agg,
        )

        return ToolResult(
            data=ConfluenceMap(
                by_tf=biases,
                aggregate_bias=agg,
                aggregate_agreement_pct=agreement,
            ),
            provenance=Provenance(
                source=f"db.ohlcv:{ctx.deps.exchange}:{symbol}:multi-tf",
                as_of=last_ts,
                rows=sum(1 for _ in biases),
                warnings=all_warnings,
            ),
        )
