"""Repository para setups (TradeIdea auto-guardadas) y su lifecycle.

Vive sobre las mismas tablas que `journal_repo.py` extendidas en migration 005:
- `journal_trades`: añade status/source/invalidation_px/targets/confidence/
  proposed_at/entry_hit_at/closed_at/dedup_hash.
- `setup_events`: audit trail de transiciones (proposed/entry_hit/sl_hit/...).

El agente llama `insert_setup_from_idea` desde el output_validator cuando emite
un TradeIdea direccional. El watcher (`app.runtime.setup_runtime`) consume
`list_open_setups` y aplica `transition_status` en cada cierre de candle.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.models import TradeIdea

SetupStatus = Literal["pending", "active", "closed", "cancelled"]
SetupEventKind = Literal[
    "proposed", "entry_hit", "tp_hit", "sl_hit",
    "expired", "manual_close", "cancelled",
]


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def derive_setup_tag(direction: str, regime_label: str, timeframe: str) -> str:
    """Tag estable derivado de (dirección, régimen, timeframe).

    Ej: 'long_trending_up_4h', 'short_ranging_1h'. Usado como strategy_id
    para agregados de winrate. Mantenerlo determinista para que el mismo
    setup en distintos días caiga bajo la misma estrategia.
    """
    return f"{direction}_{regime_label}_{timeframe}"


def _dedup_hash(
    *,
    symbol: str,
    timeframe: str,
    side: str,
    entry: float,
    invalidation: float,
    day: date,
) -> str:
    """Hash idempotente: mismos niveles aproximados en el mismo día → mismo hash.

    Bucketing a 0.5% del entry permite que refinamientos pequeños del agente
    (entry 80200 → 80250) caigan al mismo setup. Día-floor evita que el mismo
    setup propuesto mañana (con price action distinta) se elimine como dup.
    """
    bucket = max(abs(entry) * 0.005, 1e-6)
    e_rounded = round(entry / bucket) * bucket
    i_rounded = round(invalidation / bucket) * bucket
    payload = f"{symbol.upper()}|{timeframe}|{side}|{e_rounded:.6f}|{i_rounded:.6f}|{day.isoformat()}"
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _setup_summary_text(idea: TradeIdea, setup_tag: str) -> str:
    """journal_trades.summary_text es NOT NULL — la usamos como descripción
    breve del setup propuesto (no el summary_es del LLM, que tiene max 900
    chars y matices). Aquí solo lo esencial para journal+search posterior."""
    direction_es = {"long": "largo", "short": "corto", "no_trade": "sin trade"}.get(
        idea.direction, idea.direction
    )
    return (
        f"Setup {direction_es} en {idea.symbol} {idea.timeframe}. "
        f"Régimen {idea.regime.label}. Tag {setup_tag}. "
        f"Entry {idea.entry}, SL {idea.invalidation}, "
        f"TPs {len(idea.targets)}. {idea.summary_es[:300]}"
    )


# -----------------------------------------------------------------------------
# Auto-save desde TradeIdea
# -----------------------------------------------------------------------------


async def insert_setup_from_idea(
    session: AsyncSession,
    *,
    user_id: str,
    idea: TradeIdea,
    proposed_at: datetime | None = None,
) -> str | None:
    """Inserta un setup propuesto por el agente. Devuelve el id si se insertó,
    None si fue dedupado (mismo dedup_hash ya existía).

    Solo aplica a TradeIdea direccional con entry+invalidation no nulos. El
    caller debe filtrar antes (no hacemos validación duplicada aquí).
    """
    if proposed_at is None:
        proposed_at = datetime.now(tz=UTC)
    if idea.direction == "no_trade":
        return None
    if idea.entry is None or idea.invalidation is None:
        return None

    setup_tag = derive_setup_tag(idea.direction, idea.regime.label, idea.timeframe)
    dedup = _dedup_hash(
        symbol=idea.symbol,
        timeframe=idea.timeframe,
        side=idea.direction,
        entry=idea.entry,
        invalidation=idea.invalidation,
        day=proposed_at.date(),
    )

    # `targets` jsonb: [{label, price, hit_at?}]. Inicialmente sin hit_at.
    targets_payload = [
        {"label": t.label, "price": t.price, "rationale": t.rationale}
        for t in idea.targets
    ]

    # `size` por compat con schema (NOT NULL). El sizing real vive en
    # position_size_pct + leverage_x — `size` queda como notional placeholder
    # informativo. Calculamos size = position_size_pct (sin leverage) para
    # tener algo coherente. Si el modelo no lo emitió, 0.0.
    size_value = float(idea.position_size_pct or 0.0)

    row = await session.execute(
        text(
            """
            INSERT INTO journal_trades (
                user_id, trade_ts, symbol, timeframe, mode, side,
                entry_px, size, setup_tag, regime, mistakes,
                news_24h, features, summary_text, summary_hash,
                embedding_version,
                status, source, invalidation_px, targets, confidence,
                proposed_at, dedup_hash
            ) VALUES (
                :user_id, :trade_ts, :symbol, :timeframe, 'manual_log', :side,
                :entry_px, :size, :setup_tag, :regime, NULL,
                '{}'::jsonb, CAST(:features AS jsonb),
                :summary_text, :summary_hash,
                1,
                'pending', 'agent_proposal', :invalidation_px,
                CAST(:targets AS jsonb), :confidence,
                :proposed_at, :dedup_hash
            )
            ON CONFLICT (user_id, dedup_hash) WHERE dedup_hash IS NOT NULL
            DO NOTHING
            RETURNING id::text
            """
        ),
        {
            "user_id": user_id,
            "trade_ts": proposed_at,
            "symbol": idea.symbol.upper(),
            "timeframe": idea.timeframe,
            "side": idea.direction,
            "entry_px": idea.entry,
            "size": size_value,
            "setup_tag": setup_tag,
            "regime": idea.regime.label,
            "features": json.dumps({
                "leverage_x": idea.leverage_x,
                "position_size_pct": idea.position_size_pct,
                "n_scenarios": len(idea.scenarios),
                "n_confluences": len(idea.confluences),
            }),
            "summary_text": _setup_summary_text(idea, setup_tag),
            "summary_hash": dedup,  # reusa dedup como hash del summary
            "invalidation_px": idea.invalidation,
            "targets": json.dumps(targets_payload),
            "confidence": idea.confidence,
            "proposed_at": proposed_at,
            "dedup_hash": dedup,
        },
    )
    inserted = row.scalar_one_or_none()
    if inserted is None:
        return None

    # Audit event "proposed".
    await session.execute(
        text(
            """
            INSERT INTO setup_events (trade_id, event, candle_ts, payload)
            VALUES (CAST(:tid AS uuid), 'proposed', :ts, CAST(:payload AS jsonb))
            """
        ),
        {
            "tid": inserted,
            "ts": proposed_at,
            "payload": json.dumps({
                "entry": idea.entry,
                "invalidation": idea.invalidation,
                "targets": targets_payload,
                "confidence": idea.confidence,
            }),
        },
    )
    return str(inserted)


# -----------------------------------------------------------------------------
# Listings + reads
# -----------------------------------------------------------------------------


class OpenSetupRow(BaseModel):
    """Forma compacta para el watcher — solo lo que necesita para evaluar."""

    id: str
    user_id: str
    symbol: str
    timeframe: str
    side: str
    status: str
    entry_px: float
    invalidation_px: float | None
    targets: list[dict[str, Any]]
    proposed_at: datetime | None
    entry_hit_at: datetime | None


async def list_open_setups(session: AsyncSession) -> list[OpenSetupRow]:
    """Setups en estado pending/active de TODOS los usuarios. El watcher lo
    consume para saber qué (symbol, tf) escuchar y qué setups evaluar en
    cada cierre de candle. El partial index `idx_journal_trades_open` lo
    hace rápido."""
    rows = (
        await session.execute(
            text(
                """
                SELECT id::text, user_id, symbol, timeframe, side, status,
                       entry_px, invalidation_px, targets,
                       proposed_at, entry_hit_at
                FROM journal_trades
                WHERE status IN ('pending', 'active')
                  AND source = 'agent_proposal'
                """
            )
        )
    ).mappings().all()
    out: list[OpenSetupRow] = []
    for r in rows:
        out.append(
            OpenSetupRow(
                id=r["id"],
                user_id=r["user_id"],
                symbol=r["symbol"],
                timeframe=r["timeframe"],
                side=r["side"],
                status=r["status"],
                entry_px=float(r["entry_px"]),
                invalidation_px=(
                    float(r["invalidation_px"])
                    if r["invalidation_px"] is not None
                    else None
                ),
                targets=list(r["targets"] or []),
                proposed_at=r["proposed_at"],
                entry_hit_at=r["entry_hit_at"],
            )
        )
    return out


async def list_setups(
    session: AsyncSession,
    *,
    user_id: str,
    status: SetupStatus | None = None,
    symbol: str | None = None,
    source: str | None = None,
    setup_tag: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List paginated. Devuelve filas en formato dict para que el endpoint
    REST las serialice con su Pydantic model."""
    sql = """
        SELECT id::text, user_id, trade_ts, symbol, timeframe, side, status,
               source, entry_px, invalidation_px, exit_px, size, r_multiple,
               setup_tag, regime, confidence, targets, mistakes,
               proposed_at, entry_hit_at, closed_at, created_at
        FROM journal_trades
        WHERE user_id = :uid
    """
    params: dict[str, Any] = {"uid": user_id, "lim": limit, "off": offset}
    if status is not None:
        sql += " AND status = :status"
        params["status"] = status
    if symbol is not None:
        sql += " AND symbol = :symbol"
        params["symbol"] = symbol.upper()
    if source:  # None o "" → sin filtro de source (todas las fuentes)
        sql += " AND source = :source"
        params["source"] = source
    if setup_tag is not None:
        sql += " AND setup_tag = :setup_tag"
        params["setup_tag"] = setup_tag
    sql += " ORDER BY proposed_at DESC NULLS LAST, trade_ts DESC LIMIT :lim OFFSET :off"
    rows = (await session.execute(text(sql), params)).mappings().all()
    return [_normalize_row(dict(r)) for r in rows]


async def count_setups_by_status(
    session: AsyncSession,
    *,
    user_id: str,
    symbol: str | None = None,
) -> dict[str, int]:
    """Counters para el header de la pestaña Journal."""
    sql = """
        SELECT status, COUNT(*) AS n
        FROM journal_trades
        WHERE user_id = :uid AND source = 'agent_proposal'
    """
    params: dict[str, Any] = {"uid": user_id}
    if symbol is not None:
        sql += " AND symbol = :symbol"
        params["symbol"] = symbol.upper()
    sql += " GROUP BY status"
    rows = (await session.execute(text(sql), params)).all()
    out = {"pending": 0, "active": 0, "closed": 0, "cancelled": 0}
    for status, n in rows:
        out[status] = int(n)
    return out


async def get_setup_with_events(
    session: AsyncSession,
    *,
    user_id: str,
    trade_id: str,
) -> dict[str, Any] | None:
    """Detail con eventos para el side panel."""
    row = (
        await session.execute(
            text(
                """
                SELECT id::text, user_id, trade_ts, symbol, timeframe, side, status,
                       source, entry_px, invalidation_px, exit_px, size, r_multiple,
                       setup_tag, regime, confidence, targets,
                       summary_text, news_24h, features, mistakes,
                       proposed_at, entry_hit_at, closed_at, created_at, updated_at
                FROM journal_trades
                WHERE id = CAST(:tid AS uuid) AND user_id = :uid
                """
            ),
            {"tid": trade_id, "uid": user_id},
        )
    ).mappings().one_or_none()
    if not row:
        return None
    out = _normalize_row(dict(row))

    events = (
        await session.execute(
            text(
                """
                SELECT id::text, event, candle_ts, payload, created_at
                FROM setup_events
                WHERE trade_id = CAST(:tid AS uuid)
                ORDER BY candle_ts ASC
                """
            ),
            {"tid": trade_id},
        )
    ).mappings().all()
    out["events"] = [dict(e) for e in events]
    return out


# -----------------------------------------------------------------------------
# Transitions (watcher + manual override)
# -----------------------------------------------------------------------------


async def transition_status(
    session: AsyncSession,
    *,
    trade_id: str,
    new_status: SetupStatus,
    event: SetupEventKind,
    candle_ts: datetime,
    payload: dict[str, Any] | None = None,
    exit_px: float | None = None,
    r_multiple: float | None = None,
    targets_update: list[dict[str, Any]] | None = None,
) -> None:
    """Cambia status y registra setup_event en la misma transacción."""
    set_clauses = ["status = :status", "updated_at = now()"]
    params: dict[str, Any] = {"tid": trade_id, "status": new_status}

    if new_status == "active":
        set_clauses.append("entry_hit_at = :ts")
        params["ts"] = candle_ts
    if new_status == "closed":
        set_clauses.append("closed_at = :ts")
        params["ts"] = candle_ts
        if exit_px is not None:
            set_clauses.append("exit_px = :exit_px")
            params["exit_px"] = exit_px
        if r_multiple is not None:
            set_clauses.append("r_multiple = :rmul")
            params["rmul"] = r_multiple
    if targets_update is not None:
        set_clauses.append("targets = CAST(:targets AS jsonb)")
        params["targets"] = json.dumps(targets_update)

    await session.execute(
        text(
            f"""
            UPDATE journal_trades
            SET {", ".join(set_clauses)}
            WHERE id = CAST(:tid AS uuid)
            """
        ),
        params,
    )

    await session.execute(
        text(
            """
            INSERT INTO setup_events (trade_id, event, candle_ts, payload)
            VALUES (CAST(:tid AS uuid), :event, :ts, CAST(:payload AS jsonb))
            """
        ),
        {
            "tid": trade_id,
            "event": event,
            "ts": candle_ts,
            "payload": json.dumps(payload or {}),
        },
    )


async def update_targets_hits(
    session: AsyncSession,
    *,
    trade_id: str,
    targets_update: list[dict[str, Any]],
    candle_ts: datetime,
    hit_label: str,
    hit_price: float,
) -> None:
    """Marca un TP como hit_at sin cerrar el setup. Útil cuando un TP
    parcial toca pero queda(n) más TPs vivos."""
    await session.execute(
        text(
            """
            UPDATE journal_trades
            SET targets = CAST(:targets AS jsonb), updated_at = now()
            WHERE id = CAST(:tid AS uuid)
            """
        ),
        {"tid": trade_id, "targets": json.dumps(targets_update)},
    )
    await session.execute(
        text(
            """
            INSERT INTO setup_events (trade_id, event, candle_ts, payload)
            VALUES (CAST(:tid AS uuid), 'tp_hit', :ts, CAST(:payload AS jsonb))
            """
        ),
        {
            "tid": trade_id,
            "ts": candle_ts,
            "payload": json.dumps({"label": hit_label, "price": hit_price}),
        },
    )


async def cancel_setup(
    session: AsyncSession,
    *,
    user_id: str,
    trade_id: str,
) -> bool:
    """Soft-cancel de un setup. Solo aplica a status='pending' (un setup ya
    activo no se cancela — se cierra). Devuelve True si se modificó algo."""
    result = await session.execute(
        text(
            """
            UPDATE journal_trades
            SET status = 'cancelled', closed_at = now(), updated_at = now()
            WHERE id = CAST(:tid AS uuid) AND user_id = :uid AND status = 'pending'
            """
        ),
        {"tid": trade_id, "uid": user_id},
    )
    if result.rowcount == 0:
        return False
    await session.execute(
        text(
            """
            INSERT INTO setup_events (trade_id, event, candle_ts, payload)
            VALUES (CAST(:tid AS uuid), 'cancelled', now(), '{}'::jsonb)
            """
        ),
        {"tid": trade_id},
    )
    return True


# -----------------------------------------------------------------------------
# Aggregates (winrate)
# -----------------------------------------------------------------------------


async def winrate_by_setup_tag(
    session: AsyncSession,
    *,
    user_id: str,
    min_n: int = 1,
) -> list[dict[str, Any]]:
    """Agrupado por setup_tag con winrate y avg R-multiple. Solo trades
    cerrados (status='closed') cuentan; cancelled y abiertos quedan fuera."""
    rows = (
        await session.execute(
            text(
                """
                SELECT
                    setup_tag,
                    COUNT(*) AS n_closed,
                    COUNT(*) FILTER (WHERE r_multiple IS NOT NULL AND r_multiple > 0) AS n_wins,
                    AVG(r_multiple) FILTER (WHERE r_multiple IS NOT NULL) AS avg_r,
                    MAX(closed_at) AS last_closed_at
                FROM journal_trades
                WHERE user_id = :uid
                  AND status = 'closed'
                  AND setup_tag IS NOT NULL
                GROUP BY setup_tag
                HAVING COUNT(*) >= :min_n
                ORDER BY n_closed DESC, avg_r DESC NULLS LAST
                """
            ),
            {"uid": user_id, "min_n": min_n},
        )
    ).mappings().all()
    out: list[dict[str, Any]] = []
    for r in rows:
        n_closed = int(r["n_closed"])
        n_wins = int(r["n_wins"])
        win_rate = (n_wins / n_closed * 100.0) if n_closed > 0 else None
        out.append(
            {
                "setup_tag": r["setup_tag"],
                "n_closed": n_closed,
                "n_wins": n_wins,
                "win_rate_pct": round(win_rate, 1) if win_rate is not None else None,
                "avg_r": float(r["avg_r"]) if r["avg_r"] is not None else None,
                "last_closed_at": r["last_closed_at"],
            }
        )
    return out


# -----------------------------------------------------------------------------
# Internal
# -----------------------------------------------------------------------------


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    """Postgres devuelve numeric como Decimal; convertimos a float para que
    Pydantic/JSON serialicen sin sorpresas."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        if k in {
            "entry_px", "invalidation_px", "exit_px", "size", "r_multiple",
        } and v is not None:
            out[k] = float(v)
        else:
            out[k] = v
    if isinstance(out.get("targets"), str):
        # In some adapter configs jsonb comes back as text — defensive.
        try:
            out["targets"] = json.loads(out["targets"])
        except Exception:
            out["targets"] = []
    if out.get("targets") is None:
        out["targets"] = []
    return out
