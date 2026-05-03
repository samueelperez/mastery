"""get_ohlcv tool — last N closed candles for a (symbol, timeframe)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from app.agent.deps import AgentDeps
from app.agent.tools._envelope import Provenance, ToolResult
from app.agent.tools._time import floor_to_timeframe, staleness_warning
from app.storage.ohlcv_repo import fetch_range


class CandleSlim(BaseModel):
    ts: datetime
    o: float
    h: float
    l: float
    c: float
    v: float


def register_ohlcv_tools(agent: Agent[AgentDeps, object]) -> None:
    @agent.tool
    async def get_ohlcv(
        ctx: RunContext[AgentDeps],
        symbol: str,
        timeframe: Annotated[
            Literal["15m", "1h", "4h", "1d"],
            Field(description="Candle aggregation."),
        ],
        lookback: Annotated[int, Field(ge=10, le=500, description="Number of closed candles.")] = 200,
    ) -> ToolResult[list[CandleSlim]]:
        """Return the last N CLOSED candles for the symbol/timeframe.

        Use sparingly — prefer get_indicators for derived series. Useful when
        the user asks "what was the price at <time>" or to anchor a target on a
        specific high/low.
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
        candles = [CandleSlim(ts=r.ts, o=r.o, h=r.h, l=r.l, c=r.c, v=r.v) for r in rows]
        last_ts = candles[-1].ts if candles else cutoff
        warnings = []
        if w := staleness_warning(last_closed=last_ts, timeframe=timeframe):
            warnings.append(w)
        ctx.deps.log.info(
            "tool.get_ohlcv", symbol=symbol, timeframe=timeframe, rows=len(candles)
        )
        return ToolResult(
            data=candles,
            provenance=Provenance(
                source=f"db.ohlcv:{ctx.deps.exchange}:{symbol}:{timeframe}",
                as_of=last_ts,
                rows=len(candles),
                warnings=warnings,
            ),
        )
