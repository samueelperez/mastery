"""detect_bias_patterns tool — read recent bias_events; trigger a fresh run if
the table is stale (>24h since last detection).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from app.agent.deps import AgentDeps
from app.agent.tools._envelope import Provenance, ToolResult
from app.journal.bias_detector import run_for_user
from app.storage.journal_repo import list_recent_bias_events

WindowOption = Literal["7d", "30d", "90d"]
_WINDOW_DAYS = {"7d": 7, "30d": 30, "90d": 90}


class BiasFlagOut(BaseModel):
    detected_at: datetime
    kind: str
    severity: str
    payload: dict[str, Any]
    window_start: datetime
    window_end: datetime


def register_bias_tool(agent: Agent[AgentDeps, object]) -> None:
    @agent.tool
    async def detect_bias_patterns(
        ctx: RunContext[AgentDeps],
        window: Annotated[WindowOption, Field(description="Lookback for the detector run.")] = "30d",
        force_recompute: Annotated[bool, Field(description="Re-run detectors instead of reading cache.")] = False,
    ) -> ToolResult[list[BiasFlagOut]]:
        """Detect (or read cached) trading bias patterns over the last `window`.

        Heuristics covered: revenge trading, overtrading, FOMO entries (stub),
        oversize positions, disposition effect (winners cut faster than losers).

        First call (or force_recompute=True) runs the detectors and persists
        events. Subsequent calls within 24h read from cache.
        """
        async with ctx.deps.session_factory() as session:
            cached = await list_recent_bias_events(
                session, user_id=ctx.deps.user_id, limit=20
            )
            stale = (
                not cached
                or (datetime.now(tz=UTC) - cached[0].detected_at) > timedelta(hours=24)
            )
            if force_recompute or stale:
                await run_for_user(
                    session,
                    user_id=ctx.deps.user_id,
                    lookback_days=_WINDOW_DAYS[window],
                )
                cached = await list_recent_bias_events(
                    session, user_id=ctx.deps.user_id, limit=20
                )

        out = [
            BiasFlagOut(
                detected_at=e.detected_at,
                kind=e.kind,
                severity=e.severity,
                payload=e.payload,
                window_start=e.window_start,
                window_end=e.window_end,
            )
            for e in cached
        ]
        ctx.deps.log.info(
            "tool.detect_bias_patterns",
            window=window,
            n_flags=len(out),
            kinds=[f.kind for f in out],
        )
        return ToolResult(
            data=out,
            provenance=Provenance(
                source="db.bias_events",
                as_of=cached[0].detected_at if cached else datetime.fromtimestamp(0),
                rows=len(out),
                warnings=[] if cached else ["no bias events yet — journal too small"],
            ),
        )
