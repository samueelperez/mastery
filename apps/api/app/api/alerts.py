"""REST CRUD for alert rules + recent events feed.

The chat tools (`create_alert`, `list_alerts`, `delete_alert`) handle the
agent path; this router covers the UI path. Same DB, same shape.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.dsl import RuleSpec
from app.core.auth import require_user_id
from app.core.db import session_dependency

router = APIRouter()


class AlertRuleOut(BaseModel):
    id: str
    name: str
    spec: dict[str, Any]
    enabled: bool
    cooldown_s: int
    last_fired_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AlertRuleIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)
    spec: RuleSpec
    cooldown_s: int = Field(default=3600, ge=0, le=86_400)


class AlertRulePatch(BaseModel):
    enabled: bool | None = None
    cooldown_s: int | None = Field(default=None, ge=0, le=86_400)


class AlertEventOut(BaseModel):
    id: int
    rule_id: str | None
    kind: str
    severity: str
    fired_at: datetime
    snapshot: dict[str, Any]
    seen_at: datetime | None


@router.get("/alerts", response_model=list[AlertRuleOut], tags=["alerts"])
async def list_rules(
    session: Annotated[AsyncSession, Depends(session_dependency)],
    user_id: Annotated[str, Depends(require_user_id)],
    only_enabled: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[AlertRuleOut]:
    rows = (
        await session.execute(
            text(
                """
                SELECT id::text, name, spec, enabled, cooldown_s,
                       last_fired_at, created_at, updated_at
                FROM alert_rules
                WHERE user_id = :uid
                  AND (NOT :only_enabled OR enabled = true)
                ORDER BY created_at DESC
                LIMIT :lim
                """
            ),
            {"uid": user_id, "only_enabled": only_enabled, "lim": limit},
        )
    ).mappings().all()
    return [AlertRuleOut(**dict(r)) for r in rows]


@router.post("/alerts", response_model=AlertRuleOut, tags=["alerts"], status_code=201)
async def create_rule(
    body: AlertRuleIn,
    session: Annotated[AsyncSession, Depends(session_dependency)],
    user_id: Annotated[str, Depends(require_user_id)],
) -> AlertRuleOut:
    row = (
        await session.execute(
            text(
                """
                INSERT INTO alert_rules (user_id, name, spec, cooldown_s)
                VALUES (:uid, :name, CAST(:spec AS jsonb), :cd)
                RETURNING id::text, name, spec, enabled, cooldown_s,
                          last_fired_at, created_at, updated_at
                """
            ),
            {
                "uid": user_id,
                "name": body.name,
                "spec": body.spec.model_dump_json(),
                "cd": body.cooldown_s,
            },
        )
    ).mappings().one()
    await session.commit()
    return AlertRuleOut(**dict(row))


@router.patch("/alerts/{rule_id}", response_model=AlertRuleOut, tags=["alerts"])
async def patch_rule(
    rule_id: str,
    body: AlertRulePatch,
    session: Annotated[AsyncSession, Depends(session_dependency)],
    user_id: Annotated[str, Depends(require_user_id)],
) -> AlertRuleOut:
    sets: list[str] = []
    params: dict[str, Any] = {"id": rule_id, "uid": user_id}
    if body.enabled is not None:
        sets.append("enabled = :enabled")
        params["enabled"] = body.enabled
    if body.cooldown_s is not None:
        sets.append("cooldown_s = :cd")
        params["cd"] = body.cooldown_s
    if not sets:
        raise HTTPException(status_code=400, detail="no fields to update")
    sets.append("updated_at = now()")
    sql = f"""
        UPDATE alert_rules SET {", ".join(sets)}
        WHERE id = CAST(:id AS uuid) AND user_id = :uid
        RETURNING id::text, name, spec, enabled, cooldown_s,
                  last_fired_at, created_at, updated_at
    """
    row = (await session.execute(text(sql), params)).mappings().one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail=f"alert {rule_id} not found")
    await session.commit()
    return AlertRuleOut(**dict(row))


@router.delete("/alerts/{rule_id}", status_code=204, tags=["alerts"])
async def delete_rule(
    rule_id: str,
    session: Annotated[AsyncSession, Depends(session_dependency)],
    user_id: Annotated[str, Depends(require_user_id)],
) -> None:
    """Soft delete: flips enabled=false. Hard delete is intentionally absent
    so historical alert_events keep their FK link."""
    result = await session.execute(
        text(
            """
            UPDATE alert_rules
            SET enabled = false, updated_at = now()
            WHERE id = CAST(:id AS uuid) AND user_id = :uid
            """
        ),
        {"id": rule_id, "uid": user_id},
    )
    await session.commit()
    if (getattr(result, "rowcount", 0) or 0) == 0:
        raise HTTPException(status_code=404, detail=f"alert {rule_id} not found")


@router.get("/alerts/events", response_model=list[AlertEventOut], tags=["alerts"])
async def list_events(
    session: Annotated[AsyncSession, Depends(session_dependency)],
    user_id: Annotated[str, Depends(require_user_id)],
    only_unread: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[AlertEventOut]:
    rows = (
        await session.execute(
            text(
                """
                SELECT id, rule_id::text, kind, severity, fired_at, snapshot, seen_at
                FROM alert_events
                WHERE user_id = :uid
                  AND (NOT :only_unread OR seen_at IS NULL)
                ORDER BY fired_at DESC
                LIMIT :lim
                """
            ),
            {"uid": user_id, "only_unread": only_unread, "lim": limit},
        )
    ).mappings().all()
    return [AlertEventOut(**dict(r)) for r in rows]


@router.post("/alerts/events/{event_id}/seen", status_code=204, tags=["alerts"])
async def mark_event_seen(
    event_id: int,
    session: Annotated[AsyncSession, Depends(session_dependency)],
    user_id: Annotated[str, Depends(require_user_id)],
) -> None:
    result = await session.execute(
        text(
            """
            UPDATE alert_events SET seen_at = now()
            WHERE id = :id AND user_id = :uid AND seen_at IS NULL
            """
        ),
        {"id": event_id, "uid": user_id},
    )
    await session.commit()
    if (getattr(result, "rowcount", 0) or 0) == 0:
        raise HTTPException(status_code=404, detail=f"event {event_id} not found or already seen")
