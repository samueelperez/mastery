"""log_trade tool — embed and persist a closed (or open) trade to the journal."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import Field
from pydantic_ai import Agent, RunContext

from app.agent.deps import AgentDeps
from app.agent.tools._envelope import Provenance, ToolResult
from app.journal.embeddings import embed_one
from app.journal.repo import JournalTradeIn, insert_trade
from app.journal.summary import build_summary_text, hash_summary


def register_log_trade_tool(agent: Agent[AgentDeps, object]) -> None:
    @agent.tool
    async def log_trade(
        ctx: RunContext[AgentDeps],
        symbol: str,
        timeframe: Literal["15m", "1h", "4h", "1d"],
        side: Literal["long", "short"],
        entry_px: float,
        size: Annotated[float, Field(gt=0)],
        setup_tag: str,
        regime: Literal["trending_up", "trending_down", "ranging", "volatile_expansion"],
        exit_px: float | None = None,
        r_multiple: float | None = None,
        mistakes: str | None = None,
        trade_ts: datetime | None = None,
    ) -> ToolResult[dict[str, Any]]:
        """Persist a trade to the journal. Embeds the post-mortem text with
        voyage-4-large so future `get_similar_past_trades` calls can retrieve it.

        Use `mode='manual_log'` (always for this tool); paper trades from F4
        will use `mode='paper'` automatically.
        """
        symbol = symbol.upper()
        ts = trade_ts or datetime.now(tz=UTC)
        summary = build_summary_text(
            {
                "setup_tag": setup_tag,
                "regime": regime,
                "side": side,
                "symbol": symbol,
                "timeframe": timeframe,
                "r_multiple": r_multiple,
                "mistakes": mistakes,
            }
        )
        embedding = await embed_one(summary, input_type="document")

        trade = JournalTradeIn(
            user_id=ctx.deps.user_id,
            trade_ts=ts,
            symbol=symbol,
            timeframe=timeframe,
            mode="manual_log",
            side=side,
            entry_px=entry_px,
            exit_px=exit_px,
            size=size,
            r_multiple=r_multiple,
            setup_tag=setup_tag,
            regime=regime,
            mistakes=mistakes,
            summary_text=summary,
            summary_hash=hash_summary(summary),
            embedding=embedding,
            embedding_version=1,
        )
        async with ctx.deps.session_factory() as session:
            trade_id = await insert_trade(session, trade)

        ctx.deps.log.info(
            "tool.log_trade",
            trade_id=trade_id,
            symbol=symbol,
            side=side,
            r_multiple=r_multiple,
            setup_tag=setup_tag,
        )
        return ToolResult(
            data={
                "trade_id": trade_id,
                "symbol": symbol,
                "side": side,
                "summary": summary,
            },
            provenance=Provenance(
                source=f"db.journal_trades:{trade_id}",
                as_of=ts,
                rows=1,
                warnings=[],
            ),
        )
