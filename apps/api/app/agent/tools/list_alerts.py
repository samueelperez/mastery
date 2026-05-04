"""list_alerts tool — read active alert rules."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic_ai import Agent, RunContext
from sqlalchemy import text

from app.agent.deps import AgentDeps
from app.agent.tools._envelope import Provenance, ToolResult


def register_list_alerts_tool(agent: Agent[AgentDeps, object]) -> None:
    @agent.tool
    async def list_alerts(
        ctx: RunContext[AgentDeps],
        only_enabled: bool = True,
    ) -> ToolResult[dict[str, Any]]:
        """List the user's alert rules. By default only enabled ones.

        Cite this tool when claiming things like "ya tienes una alerta para
        RSI<30 en 4h" — the snapshot returns each rule's id and a compact
        summary the validator can verify against."""
        async with ctx.deps.session_factory() as session:
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT id::text, name, spec, enabled, cooldown_s,
                               last_fired_at, created_at
                        FROM alert_rules
                        WHERE user_id = :uid
                          AND (NOT :only_enabled OR enabled = true)
                        ORDER BY created_at DESC
                        LIMIT 50
                        """
                    ),
                    {"uid": ctx.deps.user_id, "only_enabled": only_enabled},
                )
            ).mappings().all()

        rules = [dict(r) for r in rows]
        return ToolResult(
            data={"rules": rules, "count": len(rules)},
            provenance=Provenance(
                source="db.alert_rules",
                as_of=datetime.now(),
                rows=len(rules),
            ),
        )
