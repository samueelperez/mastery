"""delete_alert tool — soft-delete by flipping enabled=false."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic_ai import Agent, RunContext
from sqlalchemy import text

from app.agent.deps import AgentDeps
from app.agent.tools._envelope import Provenance, ToolResult


def register_delete_alert_tool(agent: Agent[AgentDeps, object]) -> None:
    @agent.tool
    async def delete_alert(
        ctx: RunContext[AgentDeps],
        alert_id: str,
    ) -> ToolResult[dict[str, Any]]:
        """Disable an alert rule (soft delete — sets enabled=false). Pass the
        rule's id (uuid string). Returns whether the row existed."""
        async with ctx.deps.session_factory() as session:
            result = await session.execute(
                text(
                    """
                    UPDATE alert_rules
                    SET enabled = false, updated_at = now()
                    WHERE id = CAST(:id AS uuid) AND user_id = :uid
                    """
                ),
                {"id": alert_id, "uid": ctx.deps.user_id},
            )
            await session.commit()
            disabled = (getattr(result, "rowcount", 0) or 0) > 0

        ctx.deps.log.info(
            "tool.delete_alert", alert_id=alert_id, disabled=disabled
        )
        return ToolResult(
            data={"alert_id": alert_id, "disabled": disabled},
            provenance=Provenance(
                source=f"db.alert_rules:{alert_id}",
                as_of=datetime.now(tz=UTC),
                rows=1 if disabled else 0,
                warnings=[] if disabled else [f"alert_id {alert_id} not found"],
            ),
        )
