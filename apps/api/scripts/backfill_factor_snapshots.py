"""Backfill F5.5: re-corre el confluence scorer en la vela de entry de cada
trade cerrado existente y persiste `factor_snapshot`.

Sin esto, los trades cerrados pre-F5.5 quedan sin attribution y no
contribuyen a `factor_outcomes` ni a las stats Bayesian. Best-effort:
- Si el trade no tiene `entry_hit_at`, skip (no podemos computar at-entry).
- Si OHLCV no tiene velas suficientes para el TF, skip.
- Marca `factor_snapshot.version = "1-backfill"` para que las queries de
  stats puedan excluirlo opcionalmente si encuentran señales raras.

Tags semánticos NO se backfillan — son irrecuperables (el agente los emitía
en su output, ya descartado).

Uso:
    uv run python -m scripts.backfill_factor_snapshots --user-id <id>
    uv run python -m scripts.backfill_factor_snapshots --user-id <id> --dry-run

Idempotente: si el trade ya tiene `factor_snapshot` no NULL, skip por
defecto (--force para sobrescribir).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import text

from app.agent.tools.confluence import (
    compute_score_components,
    confluence_map_to_factor_snapshot_deterministic,
)
from app.core.db import dispose_engine, init_engine, session_scope

log = structlog.get_logger(__name__)


async def _list_candidates(
    *,
    user_id: str,
    force: bool,
) -> list[dict[str, Any]]:
    """Trades cerrados de `agent_proposal` con `entry_hit_at` no nulo.

    Si `force=False`, excluye los que ya tienen factor_snapshot persistido.
    """
    where = [
        "user_id = :uid",
        "source = 'agent_proposal'",
        "status IN ('closed', 'cancelled')",
        "entry_hit_at IS NOT NULL",
    ]
    if not force:
        where.append("factor_snapshot IS NULL")
    sql = f"""
        SELECT id::text, symbol, timeframe, side, regime, confidence,
               entry_hit_at, closed_at
        FROM journal_trades
        WHERE {" AND ".join(where)}
        ORDER BY entry_hit_at ASC
    """
    async with session_scope() as session:
        rows = (await session.execute(text(sql), {"uid": user_id})).mappings().all()
    return [dict(r) for r in rows]


async def _backfill_one(
    *,
    row: dict[str, Any],
    exchange: str,
    dry_run: bool,
) -> bool:
    """Computa factor_snapshot retroactivo para un trade. Devuelve True si
    persistió (o lo habría persistido en dry-run)."""
    try:
        cmap = await compute_score_components(
            session_factory=session_scope,
            exchange=exchange,
            symbol=row["symbol"],
            until=row["entry_hit_at"],
        )
    except Exception as exc:
        log.warning(
            "backfill.scorer_failed",
            trade_id=row["id"],
            symbol=row["symbol"],
            error=type(exc).__name__,
            message=str(exc)[:200],
        )
        return False

    snapshot = {
        "version": "1-backfill",
        "captured_at": datetime.now(tz=UTC).isoformat(),
        "deterministic": confluence_map_to_factor_snapshot_deterministic(cmap),
        "semantic_tags": [],  # irrecuperables del histórico
        "context": {
            "regime_label": row.get("regime"),
            "entry_tf": row.get("timeframe"),
            "backfill": True,
        },
    }

    if dry_run:
        log.info(
            "backfill.dry_run",
            trade_id=row["id"],
            symbol=row["symbol"],
            n_tfs=len(snapshot["deterministic"].get("by_tf", {})),
        )
        return True

    async with session_scope() as session:
        await session.execute(
            text(
                """
                UPDATE journal_trades
                SET factor_snapshot = CAST(:snap AS jsonb)
                WHERE id = CAST(:tid AS uuid)
                """
            ),
            {"tid": row["id"], "snap": json.dumps(snapshot)},
        )
    log.info("backfill.persisted", trade_id=row["id"], symbol=row["symbol"])
    return True


async def _run(*, user_id: str, dry_run: bool, force: bool, exchange: str) -> None:
    init_engine()
    candidates = await _list_candidates(user_id=user_id, force=force)
    log.info("backfill.start", user_id=user_id, candidates=len(candidates), dry_run=dry_run)
    ok = 0
    fail = 0
    for row in candidates:
        success = await _backfill_one(row=row, exchange=exchange, dry_run=dry_run)
        if success:
            ok += 1
        else:
            fail += 1
    log.info("backfill.done", ok=ok, fail=fail, total=len(candidates))
    await dispose_engine()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--user-id", required=True, help="User ID a procesar.")
    p.add_argument(
        "--dry-run", action="store_true",
        help="Solo loguea, no escribe en DB.",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Sobrescribir factor_snapshot existente (default skip).",
    )
    p.add_argument(
        "--exchange", default="binance_usdm",
        help="Exchange para fetch_range de OHLCV (default binance_usdm).",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(
        _run(
            user_id=args.user_id,
            dry_run=args.dry_run,
            force=args.force,
            exchange=args.exchange,
        )
    )
