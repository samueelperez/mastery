"""C.3 — Setup approval/rejection endpoints.

These are the rails for the human-in-loop step. When the Scout dispatcher
(C.1) persists a TradeIdea, the setup lands in status='pending' just like
any agent proposal. These endpoints let the user (eventually via Telegram
buttons / web push action) approve or reject.

For v1 we treat the endpoints as audit-only — the SetupRuntime already
watches pending setups and transitions them to active on entry hit. The
explicit Approve event lets us layer paper-trading authorization later
(only `approved` setups get fills simulated in the paper engine).

Endpoints:
  POST /setups/{trade_id}/approve  → writes `setup_events.event='approved'`
  POST /setups/{trade_id}/reject   → cancels setup + event='rejected_by_user'
"""

from __future__ import annotations

import json
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.auth import require_user_id
from app.db import session_scope

log = structlog.get_logger("api.setups")
router = APIRouter()


@router.post("/setups/{trade_id}/approve", tags=["setups"])
async def approve_setup(
    trade_id: str,
    user_id: Annotated[str, Depends(require_user_id)],
) -> dict[str, str]:
    """Records human approval of a pending setup. Idempotent: re-calling on
    an already-approved setup is a no-op (returns the existing state)."""
    async with session_scope() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT id::text AS id, status
                    FROM journal_trades
                    WHERE id = CAST(:tid AS uuid) AND user_id = :uid
                    """
                ),
                {"tid": trade_id, "uid": user_id},
            )
        ).mappings().one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="setup not found")
        if row["status"] != "pending":
            raise HTTPException(
                status_code=409,
                detail=f"setup status={row['status']}, only pending is approvable",
            )
        # Idempotency: check if an `approved` event already exists.
        existing = (
            await session.execute(
                text(
                    """
                    SELECT id::text FROM setup_events
                    WHERE trade_id = CAST(:tid AS uuid) AND event = 'approved'
                    LIMIT 1
                    """
                ),
                {"tid": trade_id},
            )
        ).scalar_one_or_none()
        if existing is not None:
            log.info("setup.approve.idempotent", trade_id=trade_id)
            return {"status": "already_approved", "trade_id": trade_id}
        try:
            await session.execute(
                text(
                    """
                    INSERT INTO setup_events (trade_id, event, candle_ts, payload)
                    VALUES (CAST(:tid AS uuid), 'approved', now(), CAST(:payload AS jsonb))
                    """
                ),
                {"tid": trade_id, "payload": json.dumps({"approver": user_id})},
            )
        except IntegrityError:
            # Lost a race with a concurrent /approve call. The UNIQUE index
            # `setup_events_unique_user_decision` (migration 021) caught it.
            # Treat as idempotent success: the other request already recorded
            # the approval, so both clients agree on the outcome.
            log.info("setup.approve.race_won_by_other", trade_id=trade_id)
            return {"status": "already_approved", "trade_id": trade_id}
    log.info("setup.approved", trade_id=trade_id, user_id=user_id)
    return {"status": "approved", "trade_id": trade_id}


@router.post("/setups/{trade_id}/reject", tags=["setups"])
async def reject_setup(
    trade_id: str,
    user_id: Annotated[str, Depends(require_user_id)],
) -> dict[str, str]:
    """Cancels a pending setup with `event='rejected_by_user'`. Distinct from
    `cancelled` (manual override of an arbitrary setup) so the journal can
    attribute rejections to human disapproval of a scout proposal.

    Idempotent: re-calling on an already-rejected setup returns 200 with
    `status='already_rejected'` instead of 404 (the previous behavior, which
    was a contract violation against the audit's Blocker #7).
    """
    async with session_scope() as session:
        # Look up current state first so we can give a precise reply.
        row = (
            await session.execute(
                text(
                    """
                    SELECT id::text AS id, status
                    FROM journal_trades
                    WHERE id = CAST(:tid AS uuid) AND user_id = :uid
                    """
                ),
                {"tid": trade_id, "uid": user_id},
            )
        ).mappings().one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="setup not found")
        if row["status"] == "cancelled":
            # Already rejected (or cancelled) — return idempotently.
            existing = (
                await session.execute(
                    text(
                        """
                        SELECT 1 FROM setup_events
                        WHERE trade_id = CAST(:tid AS uuid)
                          AND event = 'rejected_by_user'
                        LIMIT 1
                        """
                    ),
                    {"tid": trade_id},
                )
            ).scalar_one_or_none()
            if existing is not None:
                log.info("setup.reject.idempotent", trade_id=trade_id)
                return {"status": "already_rejected", "trade_id": trade_id}
            # Status is cancelled but reason was manual cancel — treat as
            # 409 conflict so the caller knows the setup is gone but not via
            # this endpoint.
            raise HTTPException(
                status_code=409,
                detail="setup already cancelled by a different action",
            )
        if row["status"] != "pending":
            raise HTTPException(
                status_code=409,
                detail=f"setup status={row['status']}, only pending is rejectable",
            )
        # Status guard cierra el race con SetupRuntime: si el runtime ya
        # transicionó pending → active (entry hit + approval check OK) entre
        # el SELECT inicial y este UPDATE, el WHERE status='pending' previene
        # que un reject vuelva atrás un setup ya activo.
        result = await session.execute(
            text(
                """
                UPDATE journal_trades
                SET status = 'cancelled', closed_at = now(), updated_at = now()
                WHERE id = CAST(:tid AS uuid) AND status = 'pending'
                """
            ),
            {"tid": trade_id},
        )
        if result.rowcount == 0:  # type: ignore[attr-defined]
            # Status flipped between SELECT and UPDATE — refetch to give a
            # precise reason. Most likely race: SetupRuntime activated it.
            raise HTTPException(
                status_code=409,
                detail=(
                    "setup status changed between read and write — likely "
                    "activated by the runtime in a concurrent tick. Refresh."
                ),
            )
        await session.execute(
            text(
                """
                INSERT INTO setup_events (trade_id, event, candle_ts, payload)
                VALUES (CAST(:tid AS uuid), 'rejected_by_user', now(),
                        CAST(:payload AS jsonb))
                """
            ),
            {"tid": trade_id, "payload": json.dumps({"rejected_by": user_id})},
        )
    log.info("setup.rejected", trade_id=trade_id, user_id=user_id)
    return {"status": "rejected", "trade_id": trade_id}
