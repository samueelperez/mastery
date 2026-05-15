"""Hook-shaped helpers for materialising agent outputs into setups.

Antes esta lógica vivía dentro del `output_validator` del agent (audit 2026-05).
Separar permite:
- Tests aislados de la persistencia sin construir un Agent completo.
- Llamadas desde otros sitios (futuro `routes.py` post-stream, batch import,
  etc.) sin duplicar.
- El validator queda más conciso y centra su rol: validar contratos. La
  persistencia es una consequence, no parte del contrato.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from structlog.stdlib import BoundLogger

from app.agent.models import TradeIdea
from app.setups.repo import insert_setup_from_idea


async def persist_trade_idea(
    session: AsyncSession,
    *,
    user_id: str,
    idea: TradeIdea,
    factor_snapshot: dict[str, Any] | None,
    log: BoundLogger,
) -> str | None:
    """Materialize a `TradeIdea` into the `setups` lifecycle.

    Returns the inserted setup_id, or None if dedup blocked the insert.
    Errors are caught and logged (chat response should NOT block on a journal
    write failure). Audit fix 2026-05: extracted from the agent validator.
    """
    try:
        setup_id = await insert_setup_from_idea(
            session,
            user_id=user_id,
            idea=idea,
            factor_snapshot=factor_snapshot,
        )
    except Exception as exc:
        log.warning(
            "agent.setup_persist_failed",
            error=str(exc),
            symbol=idea.symbol,
        )
        return None

    log.info(
        "agent.setup_persisted",
        setup_id=setup_id,
        deduped=setup_id is None,
        symbol=idea.symbol,
        timeframe=idea.timeframe,
        side=idea.direction,
        has_snapshot=factor_snapshot is not None,
        n_semantic_tags=(
            len(factor_snapshot.get("semantic_tags", []))
            if factor_snapshot is not None
            else 0
        ),
    )
    return setup_id


__all__ = ["persist_trade_idea"]
