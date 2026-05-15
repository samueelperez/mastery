"""Repository para post-entry trade reviews (tabla `setup_reviews`).

Cada review es un análisis estructurado generado por `review_agent` cuando el
SetupRuntime detecta un trigger relevante sobre un setup ACTIVE. El audit
event en `setup_events` (event='review_generated') vive junto con la fila
detallada en `setup_reviews` — los dos se escriben en una sola transacción
desde `insert_review` para que el timeline del UI no quede inconsistente.

Cooldown y throttling viven en columnas de `journal_trades`:
- `last_review_at`: última review persistida → drives cooldown gate.
- `last_review_price`: precio en ese momento → drives price-move guard.
- `review_count`: contador para cap REVIEW_MAX_REVIEWS_PER_SETUP.
- `next_review_at`: cuándo el time-scheduler debe re-evaluar (NULL = no due).
- `last_review_attempt_at`: backoff cuando OpenRouter falla (NO bumpea
  cooldown ni review_count, solo evita bombardeo).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.models import (
    ReviewRecommendation,
    ReviewState,
    ToolCitation,
    TradeReview,
    TriggerKind,
)

# -----------------------------------------------------------------------------
# Cooldown gate (atomic UPDATE)
# -----------------------------------------------------------------------------


async def claim_review_slot(
    session: AsyncSession,
    *,
    trade_id: str,
    cooldown_minutes: int,
    max_reviews: int,
) -> bool:
    """Atomic cooldown + cap check. Returns True iff this caller may proceed
    to invoke the review agent.

    Implementation: condicional UPDATE que bumpea `last_review_attempt_at`
    SOLO si (a) no hay attempt reciente dentro del cooldown y (b) review_count
    < cap. Si la UPDATE modifica 0 filas → otro proceso ya reclamó el slot
    o el setup excedió el cap → caller debe abortar.

    Llamar ANTES de invocar al agente. El bump real de `last_review_at` y
    `review_count` ocurre dentro de `insert_review` (path feliz). Si el
    agente falla, el caller debe llamar `release_review_claim_on_failure()`
    para no dejar el setup bloqueado por `cooldown_minutes` (audit fix
    2026-05).
    """
    result = await session.execute(
        text(
            """
            UPDATE journal_trades
            SET last_review_attempt_at = now()
            WHERE id = CAST(:tid AS uuid)
              AND review_count < :cap
              AND (
                last_review_at IS NULL
                OR last_review_at < now() - make_interval(mins => :cooldown)
              )
              AND (
                last_review_attempt_at IS NULL
                OR last_review_attempt_at < now() - make_interval(mins => :cooldown)
              )
            """
        ),
        {
            "tid": trade_id,
            "cap": max_reviews,
            "cooldown": cooldown_minutes,
        },
    )
    return int(result.rowcount) > 0  # type: ignore[attr-defined]


async def release_review_claim_on_failure(
    session: AsyncSession, *, trade_id: str
) -> None:
    """Revierte el bump de `last_review_attempt_at` cuando el agent.run() o
    persistencia falla — sin esto el setup queda bloqueado `cooldown_minutes`
    sin que ningún review se haya producido (audit fix 2026-05).

    Sólo limpia el attempt si NO hubo un review exitoso en el ínterin (el
    caller llama esto en el `except` después de un claim). Si hubo review,
    `last_review_at IS NOT NULL` y el setup ya tiene el cooldown legítimo.
    """
    await session.execute(
        text(
            """
            UPDATE journal_trades
            SET last_review_attempt_at = NULL
            WHERE id = CAST(:tid AS uuid)
              AND (
                last_review_at IS NULL
                OR last_review_at < last_review_attempt_at
              )
            """
        ),
        {"tid": trade_id},
    )


# -----------------------------------------------------------------------------
# Insert (review + audit event + cooldown bump, all in one tx)
# -----------------------------------------------------------------------------


async def insert_review(
    session: AsyncSession,
    *,
    trade_id: str,
    user_id: str,
    trigger_kind: TriggerKind,
    trigger_payload: dict[str, Any],
    review: TradeReview,
    price_at_review: float,
    model_id: str,
    usage_tokens: dict[str, Any] | None,
    cost_usd: float | None,
    prompt_version: str | None,
    next_review_at: datetime | None,
) -> str:
    """Persiste la review, escribe audit event en setup_events, bumpea
    cooldown/review_count y `next_review_at` para el time-scheduler.

    Todo en la misma transacción (el caller maneja commit/rollback con
    `async with session_scope()`).
    """
    citations_payload = [c.model_dump(mode="json") for c in review.citations]

    inserted = (
        await session.execute(
            text(
                """
                INSERT INTO setup_reviews (
                    trade_id, user_id, trigger_kind, trigger_payload,
                    current_state, recommendation, summary, rationale,
                    citations, price_at_review, model_id, usage_tokens,
                    cost_usd, prompt_version
                ) VALUES (
                    CAST(:tid AS uuid), :uid, :trigger_kind,
                    CAST(:trigger_payload AS jsonb),
                    :current_state, :recommendation, :summary, :rationale,
                    CAST(:citations AS jsonb), :price, :model,
                    CAST(:usage AS jsonb), :cost, :pv
                )
                RETURNING id::text
                """
            ),
            {
                "tid": trade_id,
                "uid": user_id,
                "trigger_kind": trigger_kind,
                "trigger_payload": json.dumps(trigger_payload),
                "current_state": review.current_state,
                "recommendation": review.recommendation,
                "summary": review.summary,
                "rationale": review.rationale,
                "citations": json.dumps(citations_payload),
                "price": price_at_review,
                "model": model_id,
                "usage": json.dumps(usage_tokens) if usage_tokens else None,
                "cost": cost_usd,
                "pv": prompt_version,
            },
        )
    ).scalar_one()

    # Audit event con payload completo. El detalle full está en
    # setup_reviews, pero el timeline del UI también persiste rationale +
    # citations + price_at_review para poder renderizar la TradeReviewCard
    # directamente desde el evento (sin fetch adicional) cuando el usuario
    # hace click en "revisión IA" → chat.
    await session.execute(
        text(
            """
            INSERT INTO setup_events (trade_id, event, candle_ts, payload)
            VALUES (
                CAST(:tid AS uuid), 'review_generated', now(),
                CAST(:payload AS jsonb)
            )
            """
        ),
        {
            "tid": trade_id,
            "payload": json.dumps(
                {
                    "review_id": inserted,
                    "trigger_kind": trigger_kind,
                    "current_state": review.current_state,
                    "recommendation": review.recommendation,
                    "summary": review.summary,
                    "rationale": review.rationale,
                    "citations": citations_payload,
                    "price_at_review": price_at_review,
                }
            ),
        },
    )

    # Bumpea cooldown + review_count + next_review_at. Esto consolida el
    # estado del setup tras un review exitoso. `last_review_attempt_at` ya
    # fue actualizado por `claim_review_slot`.
    await session.execute(
        text(
            """
            UPDATE journal_trades
            SET last_review_at = now(),
                last_review_price = :price,
                review_count = review_count + 1,
                next_review_at = :next_review_at,
                updated_at = now()
            WHERE id = CAST(:tid AS uuid)
            """
        ),
        {
            "tid": trade_id,
            "price": price_at_review,
            "next_review_at": next_review_at,
        },
    )

    return str(inserted)


# -----------------------------------------------------------------------------
# Reads
# -----------------------------------------------------------------------------


async def list_reviews_for_setup(
    session: AsyncSession,
    *,
    trade_id: str,
    user_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Reviews ordenadas por `created_at DESC`. Scope por user_id (defensa
    en profundidad — el endpoint REST ya filtra antes de llegar aquí)."""
    rows = (
        await session.execute(
            text(
                """
                SELECT id::text, trade_id::text, user_id, trigger_kind,
                       trigger_payload, current_state, recommendation,
                       summary, rationale, citations, price_at_review,
                       model_id, usage_tokens, cost_usd, prompt_version,
                       created_at
                FROM setup_reviews
                WHERE trade_id = CAST(:tid AS uuid)
                  AND user_id = :uid
                ORDER BY created_at DESC
                LIMIT :lim
                """
            ),
            {"tid": trade_id, "uid": user_id, "lim": limit},
        )
    ).mappings().all()
    return [_normalize_review_row(dict(r)) for r in rows]


async def get_review(
    session: AsyncSession,
    *,
    review_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Single review con scope por user_id."""
    row = (
        await session.execute(
            text(
                """
                SELECT id::text, trade_id::text, user_id, trigger_kind,
                       trigger_payload, current_state, recommendation,
                       summary, rationale, citations, price_at_review,
                       model_id, usage_tokens, cost_usd, prompt_version,
                       created_at
                FROM setup_reviews
                WHERE id = CAST(:rid AS uuid)
                  AND user_id = :uid
                """
            ),
            {"rid": review_id, "uid": user_id},
        )
    ).mappings().one_or_none()
    if not row:
        return None
    return _normalize_review_row(dict(row))


async def get_last_reviews(
    session: AsyncSession,
    *,
    trade_id: str,
    user_id: str,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Las N reviews más recientes — para inyectar en el user prompt del
    review_agent como contexto histórico. `user_id` es defense-in-depth
    (audit fix 2026-05: list_reviews_for_setup ya scopes, esta también)."""
    rows = (
        await session.execute(
            text(
                """
                SELECT trigger_kind, current_state, recommendation,
                       summary, created_at
                FROM setup_reviews
                WHERE trade_id = CAST(:tid AS uuid) AND user_id = :uid
                ORDER BY created_at DESC
                LIMIT :lim
                """
            ),
            {"tid": trade_id, "uid": user_id, "lim": limit},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


# -----------------------------------------------------------------------------
# Time scheduler query
# -----------------------------------------------------------------------------


async def list_active_setups_due_for_time_review(
    session: AsyncSession,
    *,
    now: datetime,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Setups ACTIVE cuya `next_review_at <= now`. El scheduler dispara
    `time_elapsed` para cada uno (en su próximo tick, cada 5 min).

    El partial index `idx_journal_trades_review_due` cubre exactamente este
    WHERE. Lee solo los campos que el dispatcher necesita para construir
    el user prompt — el detalle full vive en list_open_setups si se requiere.
    """
    rows = (
        await session.execute(
            text(
                """
                SELECT id::text, user_id, symbol, timeframe, side, status,
                       entry_px, stop_loss_px, targets,
                       proposed_at, entry_hit_at,
                       last_review_at, last_review_price, review_count,
                       next_review_at,
                       regime, confidence, summary_es_full,
                       confluences, scenarios
                FROM journal_trades
                WHERE status = 'active'
                  AND source = 'agent_proposal'
                  AND next_review_at IS NOT NULL
                  AND next_review_at <= :now
                ORDER BY next_review_at ASC
                LIMIT :lim
                """
            ),
            {"now": now, "lim": limit},
        )
    ).mappings().all()
    return [_normalize_setup_row(dict(r)) for r in rows]


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def compute_next_review_at(
    *,
    entry_hit_at: datetime | None,
    now: datetime,
    offsets_hours: tuple[int, ...],
) -> datetime | None:
    """Próximo milestone temporal después de `now`. Si todos los offsets ya
    pasaron, devuelve None (el time-scheduler deja de hacer fire por tiempo
    — los otros triggers siguen activos).
    """
    if entry_hit_at is None:
        return None
    candidates = sorted(
        (entry_hit_at + timedelta(hours=h) for h in offsets_hours),
        key=lambda dt: dt,
    )
    for c in candidates:
        if c > now:
            return c
    return None


def _normalize_review_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    if (isinstance(out.get("price_at_review"), (int, float)) is False and out.get("price_at_review") is not None) or out.get("price_at_review") is not None:
        out["price_at_review"] = float(out["price_at_review"])
    if out.get("cost_usd") is not None:
        out["cost_usd"] = float(out["cost_usd"])
    for jsonb_key in ("trigger_payload", "citations", "usage_tokens"):
        v = out.get(jsonb_key)
        if isinstance(v, str):
            try:
                out[jsonb_key] = json.loads(v)
            except Exception:
                out[jsonb_key] = None
    return out


def _normalize_setup_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for k in ("entry_px", "stop_loss_px", "last_review_price"):
        if out.get(k) is not None:
            out[k] = float(out[k])
    for jsonb_key in ("targets", "confluences", "scenarios"):
        v = out.get(jsonb_key)
        if isinstance(v, str):
            try:
                out[jsonb_key] = json.loads(v)
            except Exception:
                out[jsonb_key] = []
        if out.get(jsonb_key) is None:
            out[jsonb_key] = []
    return out


# Re-exports para que el dispatcher pueda construir TradeReview a partir de
# campos sueltos sin importar pydantic models directamente.
__all__ = [
    "ReviewRecommendation",
    "ReviewState",
    "ToolCitation",
    "TradeReview",
    "TriggerKind",
    "claim_review_slot",
    "compute_next_review_at",
    "get_last_reviews",
    "get_review",
    "insert_review",
    "list_active_setups_due_for_time_review",
    "list_reviews_for_setup",
    "release_review_claim_on_failure",
]
