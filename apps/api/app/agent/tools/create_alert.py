"""create_alert tool — register a rule with the alert engine."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import Field
from pydantic_ai import Agent, RunContext
from sqlalchemy import text

from app.agent.deps import AgentDeps
from app.agent.tools._envelope import Provenance, ToolResult
from app.alerts.dsl import RuleSpec


def register_create_alert_tool(agent: Agent[AgentDeps, object]) -> None:
    @agent.tool
    async def create_alert(
        ctx: RunContext[AgentDeps],
        name: Annotated[str, Field(min_length=2, max_length=80)],
        spec: RuleSpec,
        cooldown_s: Annotated[int, Field(ge=0, le=86_400)] = 3600,
    ) -> ToolResult[dict[str, Any]]:
        """Create a rule that fires when a candle closes meeting `spec`.

        Example specs (emit jsonb directly as the `spec` arg):

          # RSI(14) <= 30 on BTCUSDT 4h
          {"kind":"candle_close","symbol":"BTCUSDT","timeframe":"4h",
           "indicators":[{"name":"rsi","length":14}],
           "conditions":[{"left":"rsi_14","op":"<=","right":30}],
           "logic":"all"}

          # EMA21 crosses above EMA55 on 1d
          {"kind":"candle_close","symbol":"BTCUSDT","timeframe":"1d",
           "indicators":[{"name":"ema","length":21},{"name":"ema","length":55}],
           "conditions":[{"left":"ema_21","op":"cross_above","right":"ema_55"}],
           "logic":"all"}

        `cooldown_s` is the minimum gap between fires for the same rule
        (default 1h) — keeps the same condition from re-firing on consecutive
        candles. Returns the new rule's id so you can cite it.
        """
        async with ctx.deps.session_factory() as session:
            row = (
                await session.execute(
                    text(
                        """
                        INSERT INTO alert_rules (user_id, name, spec, cooldown_s)
                        VALUES ('me', :name, CAST(:spec AS jsonb), :cd)
                        RETURNING id::text, created_at
                        """
                    ),
                    {
                        "name": name,
                        "spec": spec.model_dump_json(),
                        "cd": cooldown_s,
                    },
                )
            ).mappings().one()
            await session.commit()

        ctx.deps.log.info(
            "tool.create_alert",
            alert_id=row["id"],
            name=name,
            symbol=spec.symbol,
            timeframe=spec.timeframe,
            n_conditions=len(spec.conditions),
        )
        return ToolResult(
            data={
                "alert_id": row["id"],
                "name": name,
                "spec": spec.model_dump(),
                "cooldown_s": cooldown_s,
            },
            provenance=Provenance(
                source=f"db.alert_rules:{row['id']}",
                as_of=datetime.fromisoformat(str(row["created_at"])) if not isinstance(row["created_at"], datetime) else row["created_at"],
                rows=1,
            ),
        )
