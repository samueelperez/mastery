"""C.1 + C.2 — Scout dispatcher for autonomous setup proposals.

Wired into `app/alerts/runtime.py::_evaluate_close`: when a rule with
`is_scout_trigger=TRUE` matches, the alerts runtime hands the match off here
instead of publishing to the user's alerts channel.

Pipeline:

  1. Cooldown (B.3) — `should_pause_scout`. Recent SL streaks pause the
     scout for the user/symbol.
  2. Rate limits (C.2):
     - Max `MAX_ACTIVE_PER_SYMBOL` setups in {pending, active} per symbol.
     - Max `MAX_PROPOSALS_PER_DAY` total per user in the last 24h.
  3. Agent invocation — `agent.run(synthetic_message, deps=AgentDeps(...))`.
     The synthetic user message names the rule and the matched conditions
     so the agent has context. The output goes through the same validators
     (citation contract + factor gate + R:R + slippage buffer) as the
     interactive chat.
  4. Quality floor (C.2) — for `TradeIdea` outputs:
     - `confidence != 'low'`
     - `direction in ('long', 'short')`
  5. Dedup (C.2) — skip if a pending/active setup on the same symbol+side
     exists within `2 * ATR(14)` of the proposed entry.
  6. Persist via `insert_setup_from_idea` — same code path as interactive
     chat. The SetupRuntime watcher picks it up on the next candle close.

Every drop emits a structured log with the reason; verdicts are auditable.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.deps import AgentDeps
from app.agent.models import TradeIdea
from app.alerts.cooldown import should_pause_scout
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.observability.metrics import (
    agent_invocation_seconds,
    agent_invocations_total,
    scout_accepted_total,
    scout_drops_total,
    setup_approval_outcome_total,
)
from app.liquidation.factor_snapshot import find_heatmap_citation_snapshot
from app.setups.repo import insert_setup_from_idea
from app.setups.risk_manager import fetch_atr_for_trailing
from app.setups.scout_agent import get_scout_agent

log = structlog.get_logger(__name__)


# -----------------------------------------------------------------------------
# Tunables — kept as module constants so the logic is easy to unit-test.
# -----------------------------------------------------------------------------

MAX_ACTIVE_PER_SYMBOL = 3
MAX_PROPOSALS_PER_DAY = 10
DEDUP_ATR_MULTIPLE = 2.0


DropReason = Literal[
    "cooldown_paused",
    "rate_limit_symbol",
    "rate_limit_daily",
    "quality_floor_confidence",
    "quality_floor_direction",
    "dedup_similar_pending",
    "agent_returned_brief",
    "agent_returned_text",
    "validator_raised",
    "no_trade_idea",
    "persist_error",
]


@dataclass(frozen=True)
class DispatchVerdict:
    """Auditable record of one scout invocation. `setup_id` is non-None iff
    a TradeIdea was persisted."""

    accepted: bool
    setup_id: str | None
    drop_reason: DropReason | None
    detail: str | None


def _drop(reason: DropReason, detail: str | None) -> DispatchVerdict:
    """Build a drop verdict AND increment the Prometheus counter in one place.
    Centralizing this avoids drift between the log fields and the metric
    label — every drop reason must show up in both."""
    scout_drops_total.labels(reason=reason).inc()
    return DispatchVerdict(
        accepted=False, setup_id=None, drop_reason=reason, detail=detail
    )


# -----------------------------------------------------------------------------
# Rate-limit + dedup helpers (C.2). Pure SQL — pure-function tests cover the
# logic by stubbing the session.
# -----------------------------------------------------------------------------


async def _count_active_setups_for_symbol(
    session: AsyncSession, *, user_id: str, symbol: str
) -> int:
    row = (
        await session.execute(
            text(
                """
                SELECT COUNT(*) AS n
                FROM journal_trades
                WHERE user_id = :uid
                  AND source IN ('agent_proposal', 'scout_proposal')
                  AND symbol = :sym
                  AND status IN ('pending', 'active')
                """
            ),
            {"uid": user_id, "sym": symbol.upper()},
        )
    ).mappings().one()
    return int(row["n"])


async def _count_proposals_in_last_24h(
    session: AsyncSession, *, user_id: str
) -> int:
    row = (
        await session.execute(
            text(
                """
                SELECT COUNT(*) AS n
                FROM journal_trades
                WHERE user_id = :uid
                  AND source IN ('agent_proposal', 'scout_proposal')
                  AND proposed_at >= now() - interval '24 hours'
                """
            ),
            {"uid": user_id},
        )
    ).mappings().one()
    return int(row["n"])


async def _find_similar_open_setup(
    session: AsyncSession,
    *,
    user_id: str,
    symbol: str,
    side: str,
    entry: float,
    atr_distance: float,
) -> str | None:
    """Returns the id of an existing pending/active setup on the same
    (symbol, side) whose entry is within `atr_distance` of the candidate
    entry. None if no dup. We pass ATR-derived distance so the gate adapts
    to volatility regimes (wider for SOL, tighter for BTC)."""
    row = (
        await session.execute(
            text(
                """
                SELECT id::text AS id
                FROM journal_trades
                WHERE user_id = :uid
                  AND source IN ('agent_proposal', 'scout_proposal')
                  AND symbol = :sym
                  AND side = :side
                  AND status IN ('pending', 'active')
                  AND ABS(entry_px - :entry) <= :dist
                LIMIT 1
                """
            ),
            {
                "uid": user_id,
                "sym": symbol.upper(),
                "side": side,
                "entry": entry,
                "dist": atr_distance,
            },
        )
    ).mappings().one_or_none()
    return row["id"] if row else None


# -----------------------------------------------------------------------------
# Main entry point
# -----------------------------------------------------------------------------


def _build_user_message(
    *,
    rule_name: str,
    symbol: str,
    timeframe: str,
    snapshot: dict[str, Any],
) -> str:
    """Synthetic user message for the agent. Keeps the framing consistent so
    the agent recognises the scout context and applies its standard pipeline
    (tools → confluence → TradeIdea or no_trade)."""
    snapshot_keys = ", ".join(sorted(snapshot.keys())[:6])
    return (
        f"[scout-trigger] Scanner rule '{rule_name}' acaba de hacer match en "
        f"{symbol}@{timeframe}. Snapshot keys: {snapshot_keys}. "
        f"Evalúa el setup: cita tools, aplica gates de R:R, slippage, citation "
        f"contract y factor gate. Emite TradeIdea si hay edge claro o "
        f"BriefAnalysis con explicación si no. NO inventes niveles — todo "
        f"valor numérico debe venir de una tool con `tool_name` citado."
    )


async def dispatch_scout_match(
    *,
    user_id: str,
    rule_id: str,
    rule_name: str,
    symbol: str,
    timeframe: str,
    snapshot: dict[str, Any],
    fired_at: datetime,
) -> DispatchVerdict:
    """Entry called by `alerts/runtime.py` when a scout-tagged rule matches.

    Idempotent at the SetupRepo layer (insert_setup_from_idea uses dedup_hash
    so two near-identical matches in a row collapse to one setup).
    Side-effecting: writes to journal_trades + setup_events on success;
    nothing on drop. Returns DispatchVerdict for the caller to log.
    """
    bound_log = log.bind(
        scout_rule_id=rule_id,
        scout_rule=rule_name,
        user_id=user_id,
        symbol=symbol,
        timeframe=timeframe,
    )

    # --- 1. Cooldown (B.3) ----------------------------------------------------
    async with session_scope() as session:
        cooldown = await should_pause_scout(
            session, user_id=user_id, symbol=symbol
        )
    if cooldown.paused:
        bound_log.info(
            "scout.dropped.cooldown",
            scope=cooldown.scope,
            consec=cooldown.consecutive_losses,
            ends_at=cooldown.ends_at.isoformat() if cooldown.ends_at else None,
        )
        return _drop("cooldown_paused", cooldown.reason)

    # --- 2. Rate limits (C.2) -------------------------------------------------
    async with session_scope() as session:
        n_symbol = await _count_active_setups_for_symbol(
            session, user_id=user_id, symbol=symbol
        )
        n_24h = await _count_proposals_in_last_24h(session, user_id=user_id)
    if n_symbol >= MAX_ACTIVE_PER_SYMBOL:
        bound_log.info("scout.dropped.rate_limit_symbol", n_active=n_symbol)
        return _drop(
            "rate_limit_symbol",
            f"{n_symbol} setups activos/pendientes en {symbol} (max {MAX_ACTIVE_PER_SYMBOL})",
        )
    if n_24h >= MAX_PROPOSALS_PER_DAY:
        bound_log.info("scout.dropped.rate_limit_daily", n_24h=n_24h)
        return _drop(
            "rate_limit_daily",
            f"{n_24h} propuestas en 24h (max {MAX_PROPOSALS_PER_DAY})",
        )

    # --- 3. Agent invocation --------------------------------------------------
    deps = AgentDeps(
        session_factory=session_scope,
        log=bound_log,
        user_id=user_id,
    )
    user_message = _build_user_message(
        rule_name=rule_name, symbol=symbol, timeframe=timeframe, snapshot=snapshot,
    )
    started = time.monotonic()
    try:
        # Dedicated Haiku 4.5 agent (PR-07 / ADR-003). Same tool catalogue
        # and validators as the main chat agent; the cost difference (~10×)
        # is the entire point of the migration.
        result = await get_scout_agent().run(user_message, deps=deps)
    except Exception as exc:
        agent_invocation_seconds.labels(kind="scout").observe(
            time.monotonic() - started
        )
        agent_invocations_total.labels(kind="scout", outcome="error").inc()
        # `.exception` (vs `.warning`) preserves the full traceback so ops can
        # distinguish a validator ModelRetry from a network timeout, OOM, or
        # genuine code bug — `validator_raised` is a catch-all by design.
        bound_log.exception(
            "scout.dropped.validator_raised",
            error=f"{type(exc).__name__}: {str(exc)[:200]}",
        )
        return _drop("validator_raised", type(exc).__name__)
    agent_invocation_seconds.labels(kind="scout").observe(
        time.monotonic() - started
    )

    output = result.output

    # --- 4. Output gating (C.2 quality floor) --------------------------------
    if not isinstance(output, TradeIdea):
        kind = "brief" if output is not None and hasattr(output, "summary_es") else "text"
        drop_reason: DropReason = (
            "agent_returned_brief" if kind == "brief" else "agent_returned_text"
        )
        agent_invocations_total.labels(kind="scout", outcome=kind).inc()
        bound_log.info("scout.dropped.no_trade_idea", kind=kind)
        return _drop(drop_reason, kind)
    agent_invocations_total.labels(kind="scout", outcome="trade_idea").inc()
    if output.direction == "no_trade":
        bound_log.info("scout.dropped.quality_floor_direction", direction="no_trade")
        return _drop("quality_floor_direction", "agent emitted no_trade")
    if output.confidence == "low":
        bound_log.info("scout.dropped.quality_floor_confidence", confidence="low")
        return _drop("quality_floor_confidence", "confidence=low under scout floor")

    # --- 5. Dedup (C.2) -------------------------------------------------------
    # Use ATR as the proximity metric. Skip dedup if ATR unavailable —
    # rather have a near-dup than block on infrastructure.
    if output.entry is not None:
        try:
            async with session_scope() as session:
                atr_value = await fetch_atr_for_trailing(
                    session,
                    symbol=output.symbol,
                    timeframe=output.timeframe,
                    candle_ts=fired_at,
                )
        except Exception as exc:
            bound_log.warning("scout.atr_fetch_failed", error=str(exc))
            atr_value = None
        if atr_value is not None:
            atr_distance = atr_value * DEDUP_ATR_MULTIPLE
            async with session_scope() as session:
                dup_id = await _find_similar_open_setup(
                    session,
                    user_id=user_id,
                    symbol=output.symbol,
                    side=output.direction,
                    entry=output.entry,
                    atr_distance=atr_distance,
                )
            if dup_id is not None:
                bound_log.info(
                    "scout.dropped.dedup",
                    dup_setup_id=dup_id,
                    atr=atr_value,
                    threshold=atr_distance,
                )
                return _drop(
                    "dedup_similar_pending",
                    f"within {DEDUP_ATR_MULTIPLE}x ATR of {dup_id}",
                )

    # --- 6. Persist ----------------------------------------------------------
    # Wrapped in try/except: DB hiccup must not crash the dispatcher (else the
    # asyncio task dies silently and Valkey publishes nothing to alert ops).
    # On failure we emit a `persist_error` verdict that callers can log + alert on.
    proposed_at = datetime.now(tz=UTC)
    try:
        async with session_scope() as session:
            setup_id = await insert_setup_from_idea(
                session,
                user_id=user_id,
                idea=output,
                proposed_at=proposed_at,
                source="scout_proposal",
            )
    except Exception as exc:
        bound_log.exception(
            "scout.persist_error",
            error=f"{type(exc).__name__}: {str(exc)[:200]}",
        )
        return _drop("persist_error", type(exc).__name__)
    if setup_id is None:
        bound_log.info("scout.dropped.dedup_hash")
        return _drop(
            "dedup_similar_pending", "setup_repo dedup_hash collision"
        )

    scout_accepted_total.inc()

    bound_log.info(
        "scout.accepted",
        setup_id=setup_id,
        direction=output.direction,
        confidence=output.confidence,
    )

    # --- 7. Auto-approve gate (M1-polish) ------------------------------------
    # When the proposed setup carries very-high agreement on the heatmap AND
    # the agent emitted confidence='high', the human-in-loop step is bypassed.
    # The operator still gets a fire-and-forget notification (so they know
    # what fired) but the interactive Approve/Reject buttons are skipped.
    auto_approved = await _maybe_auto_approve(
        setup_id=setup_id, idea=output, log=bound_log
    )

    # C.3 — Telegram notification fire-and-forget. Failure (no token, network
    # error, user unlinked) NEVER blocks the dispatch — the setup is already
    # persisted and visible in the UI. send_setup_alert swallows its own
    # exceptions and logs them.
    try:
        from app.notifications.repo import get_telegram_chat_id
        from app.notifications.telegram import send_setup_alert, send_text

        async with session_scope() as session:
            chat_id = await get_telegram_chat_id(session, user_id=user_id)
        if chat_id:
            if auto_approved:
                # Auto-approved: send a plain notification, no buttons. The
                # operator can still cancel via the web UI; the regular
                # SetupRuntime watcher handles activation on entry hit.
                await send_text(
                    chat_id,
                    f"🤖 Auto-approved {output.direction.upper()} "
                    f"{output.symbol} {output.timeframe} "
                    f"(confidence={output.confidence}, "
                    f"agreement≥{settings_snapshot_agreement(output)}).",
                )
            else:
                await send_setup_alert(
                    chat_id=chat_id, setup_id=setup_id, idea=output
                )
    except Exception as exc:
        bound_log.warning(
            "scout.notify_failed",
            setup_id=setup_id,
            error=f"{type(exc).__name__}: {exc}",
        )

    return DispatchVerdict(
        accepted=True,
        setup_id=setup_id,
        drop_reason=None,
        detail=None,
    )


# -----------------------------------------------------------------------------
# Auto-approve helpers
# -----------------------------------------------------------------------------


def _heatmap_agreement(idea: TradeIdea) -> float | None:
    """Pull `sources_agreement` from the heatmap citation on a TradeIdea.
    None when the idea didn't cite the heatmap (e.g. no Cerebro 1 zone) or
    the citation snapshot lacks the field."""
    snap = find_heatmap_citation_snapshot(idea)
    if snap is None:
        return None
    raw = snap.get("sources_agreement")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def settings_snapshot_agreement(idea: TradeIdea) -> str:
    """Render the agreement value (or '—') for the Telegram notification."""
    a = _heatmap_agreement(idea)
    return f"{a:.2f}" if a is not None else "—"


async def _maybe_auto_approve(
    *,
    setup_id: str,
    idea: TradeIdea,
    log: Any,
) -> bool:
    """Insert an `approved` event with payload marking the auto-approval
    when the idea satisfies the configured gate. Returns True iff the
    setup was auto-approved (caller adjusts the Telegram notification
    accordingly).

    Idempotency-safe — the existing partial unique index
    `setup_events_unique_user_decision` (migration 021) prevents double
    inserts; if a manual `/approve` raced ahead we silently treat as
    already-approved.
    """
    cfg = get_settings()
    if idea.confidence != cfg.auto_approve_min_confidence:
        return False
    agreement = _heatmap_agreement(idea)
    if agreement is None or agreement < cfg.auto_approve_min_agreement:
        return False

    payload = {
        "approver": "scout_auto",
        "auto": True,
        "agreement": agreement,
        "confidence": idea.confidence,
    }
    try:
        async with session_scope() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO setup_events (trade_id, event, candle_ts, payload)
                    VALUES (CAST(:tid AS uuid), 'approved', now(), CAST(:payload AS jsonb))
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"tid": setup_id, "payload": json.dumps(payload)},
            )
    except Exception as exc:  # pragma: no cover — defensive against DB hiccups
        log.warning(
            "scout.auto_approve_failed",
            setup_id=setup_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        return False
    setup_approval_outcome_total.labels(outcome="auto_approved").inc()
    log.info(
        "scout.auto_approved",
        setup_id=setup_id,
        agreement=agreement,
        confidence=idea.confidence,
    )
    return True
