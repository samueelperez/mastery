"""Repository para setups (TradeIdea auto-guardadas) y su lifecycle.

Vive sobre las mismas tablas que `journal_repo.py` extendidas en migration 005:
- `journal_trades`: añade status/source/stop_loss_px/targets/confidence/
  proposed_at/entry_hit_at/closed_at/dedup_hash.
- `setup_events`: audit trail de transiciones (proposed/entry_hit/sl_hit/...).

El agente llama `insert_setup_from_idea` desde el output_validator cuando emite
un TradeIdea direccional. El watcher (`app.setups.runtime`) consume
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
from app.backtest.factor_stats_repo import PRESENT_THRESHOLD
from app.core.observability.metrics import setup_transitions_total

SetupStatus = Literal["pending", "active", "closed", "cancelled"]
SetupEventKind = Literal[
    "proposed", "entry_hit", "tp_hit", "sl_hit",
    "expired", "manual_close", "cancelled", "invalidated",
    # B.1 RiskManager events (migration 016).
    "be_moved", "trailing_updated", "time_stopped",
]

# EXT-4: % de trades cerrados que entran al bucket holdout (out-of-sample).
# Determinista vía hash(trade_id || user_id). Trades holdout NUNCA entran al
# preamble del agente ni a get_factor_hit_rates por defecto — sólo se consultan
# en /api/journal/holdout-performance para detectar drift.
DEFAULT_HOLDOUT_PCT = 15


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
    stop_loss: float,
    day: date,
) -> str:
    """Hash idempotente: mismos niveles aproximados en el mismo día → mismo hash.

    Bucketing a 0.5% del entry permite que refinamientos pequeños del agente
    (entry 80200 → 80250) caigan al mismo setup. Día-floor evita que el mismo
    setup propuesto mañana (con price action distinta) se elimine como dup.

    Las `invalidation_conditions` NO entran al hash a propósito: el agente
    puede refinar condiciones sin que cuente como un setup distinto.
    """
    bucket = max(abs(entry) * 0.005, 1e-6)
    e_rounded = round(entry / bucket) * bucket
    s_rounded = round(stop_loss / bucket) * bucket
    payload = f"{symbol.upper()}|{timeframe}|{side}|{e_rounded:.6f}|{s_rounded:.6f}|{day.isoformat()}"
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
        f"Entry {idea.entry}, SL {idea.stop_loss}, "
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
    factor_snapshot: dict[str, Any] | None = None,
    source: str = "agent_proposal",
) -> str | None:
    """Inserta un setup propuesto por el agente. Devuelve el id si se insertó,
    None si fue dedupado (mismo dedup_hash ya existía).

    Solo aplica a TradeIdea direccional con entry+stop_loss no nulos. El
    caller debe filtrar antes (no hacemos validación duplicada aquí).

    `factor_snapshot` (F5.5): captura los ScoreComponents deterministic del
    confluence scorer + semantic_tags del agente + contexto al proposal. Si
    None, el setup queda sin attribution — los trades cerrados que vengan
    de filas con NULL factor_snapshot NO se fan-out a `factor_outcomes`.
    """
    if proposed_at is None:
        proposed_at = datetime.now(tz=UTC)
    if idea.direction == "no_trade":
        return None
    if idea.entry is None or idea.stop_loss is None:
        return None

    setup_tag = derive_setup_tag(idea.direction, idea.regime.label, idea.timeframe)
    dedup = _dedup_hash(
        symbol=idea.symbol,
        timeframe=idea.timeframe,
        side=idea.direction,
        entry=idea.entry,
        stop_loss=idea.stop_loss,
        day=proposed_at.date(),
    )

    # `targets` jsonb: [{label, price, hit_at?}]. Inicialmente sin hit_at.
    targets_payload = [
        {"label": t.label, "price": t.price, "rationale": t.rationale}
        for t in idea.targets
    ]

    # Invalidation conditions y expires_at: persistimos el shape verbatim
    # del modelo Pydantic. SetupRuntime re-valida cada `spec` como
    # `RuleSpec.model_validate(...)` en tiempo de evaluación; aquí
    # almacenamos el JSON tal cual ya validado por el agent validator.
    conditions_payload = [
        c.model_dump(mode="json") for c in idea.invalidation_conditions
    ]
    expires_at_citations_payload: list[dict[str, Any]] | None = (
        [c.model_dump(mode="json") for c in idea.expires_at_citations]
        if idea.expires_at is not None and idea.expires_at_citations
        else None
    )

    # Tesis narrativa para post-entry reviews. Descartamos citations de las
    # confluencias (son stale segundos después del proposal — el review
    # agent re-llama tools para data fresh). Mantenemos el contenido
    # interpretable: TF, bias, narrative, escenarios con probabilidades.
    confluences_payload = [
        {
            "timeframe": c.timeframe,
            "bias": c.bias,
            "narrative": c.narrative,
        }
        for c in idea.confluences
    ]
    scenarios_payload = [
        {
            "label": s.label,
            "probability_pct": s.probability_pct,
            "description": s.description,
            "entry": s.entry,
            "stop_loss": s.stop_loss,
            "target": s.target,
        }
        for s in idea.scenarios
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
                status, source, stop_loss_px, targets, confidence,
                proposed_at, dedup_hash,
                invalidation_conditions, expires_at,
                expires_at_rationale, expires_at_citations,
                summary_es_full, confluences, scenarios,
                factor_snapshot
            ) VALUES (
                :user_id, :trade_ts, :symbol, :timeframe, 'manual_log', :side,
                :entry_px, :size, :setup_tag, :regime, NULL,
                '{}'::jsonb, CAST(:features AS jsonb),
                :summary_text, :summary_hash,
                1,
                'pending', :source, :stop_loss_px,
                CAST(:targets AS jsonb), :confidence,
                :proposed_at, :dedup_hash,
                CAST(:invalidation_conditions AS jsonb), :expires_at,
                :expires_at_rationale,
                CAST(:expires_at_citations AS jsonb),
                :summary_es_full,
                CAST(:confluences AS jsonb),
                CAST(:scenarios AS jsonb),
                CAST(:factor_snapshot AS jsonb)
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
            "stop_loss_px": idea.stop_loss,
            "targets": json.dumps(targets_payload),
            "confidence": idea.confidence,
            "proposed_at": proposed_at,
            "dedup_hash": dedup,
            "invalidation_conditions": json.dumps(conditions_payload),
            "expires_at": idea.expires_at,
            "expires_at_rationale": idea.expires_at_rationale,
            "expires_at_citations": (
                json.dumps(expires_at_citations_payload)
                if expires_at_citations_payload is not None
                else None
            ),
            "summary_es_full": idea.summary_es,
            "confluences": json.dumps(confluences_payload),
            "scenarios": json.dumps(scenarios_payload),
            "factor_snapshot": (
                json.dumps(factor_snapshot) if factor_snapshot is not None else None
            ),
            "source": source,
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
                "stop_loss": idea.stop_loss,
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
    """Forma compacta para el watcher — solo lo que necesita para evaluar.

    Incluye la **tesis narrativa** original (regime, confidence, summary_es,
    confluences, scenarios) para que el review_dispatcher pueda inyectarla
    en el user prompt del review_agent y juzgar "¿se mantiene la tesis?"
    sin tener que re-derivarla vía tools."""

    id: str
    user_id: str
    symbol: str
    timeframe: str
    side: str
    status: str
    entry_px: float
    stop_loss_px: float | None
    targets: list[dict[str, Any]]
    invalidation_conditions: list[dict[str, Any]]
    expires_at: datetime | None
    proposed_at: datetime | None
    entry_hit_at: datetime | None
    # Tesis narrativa — todos opcionales para tolerar filas pre-migration 010.
    regime: str | None = None
    confidence: str | None = None
    summary_es_full: str | None = None
    confluences: list[dict[str, Any]] = []
    scenarios: list[dict[str, Any]] = []
    # B.1: estado del RiskManager (migration 016). Default `{}` mantiene
    # compat con filas pre-migration (donde la columna no existía).
    risk_state: dict[str, Any] = {}
    # C.3 Blocker 1: distingue scout vs agent proposals para el approval gate
    # en SetupRuntime. 'agent_proposal' = chat interactivo (sin gate),
    # 'scout_proposal' = scout autónomo (requiere approval).
    source: str = "agent_proposal"


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
                       entry_px, stop_loss_px, targets,
                       invalidation_conditions, expires_at,
                       proposed_at, entry_hit_at,
                       regime, confidence, summary_es_full,
                       confluences, scenarios,
                       risk_state, source
                FROM journal_trades
                WHERE status IN ('pending', 'active')
                  AND source IN ('agent_proposal', 'scout_proposal')
                """
            )
        )
    ).mappings().all()
    out: list[OpenSetupRow] = []
    for r in rows:
        # risk_state puede venir como dict (asyncpg) o str (legacy). Normalizamos.
        rs_raw = r.get("risk_state")
        if isinstance(rs_raw, str):
            try:
                risk_state = json.loads(rs_raw)
            except Exception:
                risk_state = {}
        elif isinstance(rs_raw, dict):
            risk_state = rs_raw
        else:
            risk_state = {}
        out.append(
            OpenSetupRow(
                id=r["id"],
                user_id=r["user_id"],
                symbol=r["symbol"],
                timeframe=r["timeframe"],
                side=r["side"],
                status=r["status"],
                entry_px=float(r["entry_px"]),
                stop_loss_px=(
                    float(r["stop_loss_px"])
                    if r["stop_loss_px"] is not None
                    else None
                ),
                targets=list(r["targets"] or []),
                invalidation_conditions=list(r["invalidation_conditions"] or []),
                expires_at=r["expires_at"],
                proposed_at=r["proposed_at"],
                entry_hit_at=r["entry_hit_at"],
                regime=r.get("regime"),
                confidence=r.get("confidence"),
                summary_es_full=r.get("summary_es_full"),
                confluences=list(r.get("confluences") or []),
                scenarios=list(r.get("scenarios") or []),
                risk_state=risk_state,
                source=r.get("source") or "agent_proposal",
            )
        )
    return out


async def fetch_setup_by_id(
    session: AsyncSession,
    *,
    trade_id: str,
    user_id: str,
) -> OpenSetupRow | None:
    """Fetch a single setup by id scoped by user_id, regardless of status.

    Used by the manual analyze endpoint — returns rows even when status
    is closed/cancelled (the reviewer accepts any state under manual=True)."""
    row = (
        await session.execute(
            text(
                """
                SELECT id::text, user_id, symbol, timeframe, side, status,
                       entry_px, stop_loss_px, targets,
                       invalidation_conditions, expires_at,
                       proposed_at, entry_hit_at,
                       regime, confidence, summary_es_full,
                       confluences, scenarios,
                       risk_state, source
                FROM journal_trades
                WHERE id = CAST(:tid AS uuid) AND user_id = :uid
                """
            ),
            {"tid": trade_id, "uid": user_id},
        )
    ).mappings().one_or_none()
    if row is None:
        return None
    rs_raw = row.get("risk_state")
    if isinstance(rs_raw, str):
        try:
            risk_state = json.loads(rs_raw)
        except Exception:
            risk_state = {}
    elif isinstance(rs_raw, dict):
        risk_state = rs_raw
    else:
        risk_state = {}
    return OpenSetupRow(
        id=row["id"],
        user_id=row["user_id"],
        symbol=row["symbol"],
        timeframe=row["timeframe"],
        side=row["side"],
        status=row["status"],
        entry_px=float(row["entry_px"]),
        stop_loss_px=(
            float(row["stop_loss_px"]) if row["stop_loss_px"] is not None else None
        ),
        targets=list(row["targets"] or []),
        invalidation_conditions=list(row["invalidation_conditions"] or []),
        expires_at=row["expires_at"],
        proposed_at=row["proposed_at"],
        entry_hit_at=row["entry_hit_at"],
        regime=row.get("regime"),
        confidence=row.get("confidence"),
        summary_es_full=row.get("summary_es_full"),
        confluences=list(row.get("confluences") or []),
        scenarios=list(row.get("scenarios") or []),
        risk_state=risk_state,
        source=row.get("source") or "agent_proposal",
    )


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
               source, entry_px, stop_loss_px, exit_px, size, r_multiple,
               setup_tag, regime, confidence, targets, mistakes,
               invalidation_conditions, expires_at,
               proposed_at, entry_hit_at, closed_at, invalidated_at, created_at
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
        # 'agent_proposal' es el valor histórico que el frontend pasa para
        # "todos los setups del bot"; tras C.1 esos se reparten en
        # 'agent_proposal' (chat) y 'scout_proposal' (autónomos). Ampliamos
        # el filtro para que el journal siga mostrando ambos sin cambios
        # en el cliente.
        if source == "agent_proposal":
            sql += " AND source IN ('agent_proposal', 'scout_proposal')"
        else:
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
    """Counters para el header de la pestaña Journal. Incluye ambos
    bot-originated sources (agent chat + scout) — manuales y CSV quedan fuera."""
    sql = """
        SELECT status, COUNT(*) AS n
        FROM journal_trades
        WHERE user_id = :uid
          AND source IN ('agent_proposal', 'scout_proposal')
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
                       source, entry_px, stop_loss_px, exit_px, size, r_multiple,
                       setup_tag, regime, confidence, targets,
                       summary_text, summary_es_full, news_24h, features, mistakes,
                       invalidation_conditions, expires_at,
                       expires_at_rationale, expires_at_citations,
                       proposed_at, entry_hit_at, closed_at, invalidated_at,
                       created_at, updated_at
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


def compute_is_holdout(
    *, trade_id: str, user_id: str, holdout_pct: int = DEFAULT_HOLDOUT_PCT
) -> bool:
    """Split determinista por hash(trade_id || user_id). Mismo trade siempre
    cae al mismo bucket — sin data leakage temporal (no depende de cuándo
    cerró). El bucket es global por usuario; `holdout_pct=15` reserva ~15%
    de los cierres para validación out-of-sample del feedback loop.
    """
    if holdout_pct <= 0:
        return False
    if holdout_pct >= 100:
        return True
    h = hashlib.sha256(f"{trade_id}|{user_id}".encode()).hexdigest()
    return (int(h[:8], 16) % 100) < holdout_pct


def _classify_outcome(
    *, r_multiple: float | None, exit_reason: str, all_tps_hit: bool
) -> str:
    """Mapea r_multiple → outcome bucket (mismas reglas que migración 012)."""
    if r_multiple is None:
        return "loss"
    if exit_reason == "manual_close" and r_multiple > 0 and not all_tps_hit:
        return "partial_win"
    if r_multiple > 0.2:
        return "win"
    if r_multiple > 0:
        return "breakeven"
    return "loss"


async def _fanout_factor_outcomes(
    session: AsyncSession,
    *,
    trade_id: str,
    user_id: str,
    symbol: str,
    timeframe: str,
    factor_snapshot: dict[str, Any],
    r_multiple: float,
    outcome: str,
    is_holdout: bool,
    closed_at: datetime,
) -> int:
    """Desempaqueta factor_snapshot a N filas en factor_outcomes.

    Para cada (factor_name, factor_tf) en `deterministic.by_tf` y cada tag
    en `semantic_tags` inserta una fila. ON CONFLICT DO NOTHING garantiza
    idempotencia (mismas UNIQUE keys). Devuelve nº de filas insertadas.
    """
    rows_inserted = 0
    regime_label = (
        factor_snapshot.get("context", {}).get("regime_label")
        if isinstance(factor_snapshot.get("context"), dict)
        else None
    )

    deterministic = factor_snapshot.get("deterministic") or {}
    by_tf = deterministic.get("by_tf") or {}
    if isinstance(by_tf, dict):
        for factor_tf, components in by_tf.items():
            if not isinstance(components, dict):
                continue
            for factor_name, value in components.items():
                # `score_total` no es un factor, es el agregado. Skip.
                if factor_name == "score_total":
                    continue
                try:
                    fvalue = float(value)
                except (TypeError, ValueError):
                    continue
                present = abs(fvalue) >= PRESENT_THRESHOLD
                result = await session.execute(
                    text(
                        """
                        INSERT INTO factor_outcomes (
                            trade_id, user_id, symbol, timeframe,
                            factor_name, factor_tf, factor_kind,
                            factor_value, factor_present, regime_label,
                            r_multiple, outcome, is_holdout, closed_at
                        ) VALUES (
                            CAST(:tid AS uuid), :uid, :symbol, :timeframe,
                            :fname, :ftf, 'deterministic',
                            :fvalue, :fpresent, :rl,
                            :r, :outcome, :hold, :ts
                        )
                        ON CONFLICT (trade_id, factor_name, factor_tf)
                        DO NOTHING
                        """
                    ),
                    {
                        "tid": trade_id, "uid": user_id, "symbol": symbol,
                        "timeframe": timeframe,
                        "fname": factor_name, "ftf": factor_tf,
                        "fvalue": fvalue, "fpresent": present, "rl": regime_label,
                        "r": r_multiple, "outcome": outcome, "hold": is_holdout,
                        "ts": closed_at,
                    },
                )
                if result.rowcount:
                    rows_inserted += int(result.rowcount)

    # Semantic tags: factor_kind='semantic', factor_tf=NULL, factor_value=NULL,
    # factor_present=TRUE (el agente sólo emite tags que considera presentes).
    semantic_tags = factor_snapshot.get("semantic_tags") or []
    if isinstance(semantic_tags, list):
        for tag in semantic_tags:
            if not isinstance(tag, str) or not tag:
                continue
            result = await session.execute(
                text(
                    """
                    INSERT INTO factor_outcomes (
                        trade_id, user_id, symbol, timeframe,
                        factor_name, factor_tf, factor_kind,
                        factor_value, factor_present, regime_label,
                        r_multiple, outcome, is_holdout, closed_at
                    ) VALUES (
                        CAST(:tid AS uuid), :uid, :symbol, :timeframe,
                        :fname, NULL, 'semantic',
                        NULL, TRUE, :rl,
                        :r, :outcome, :hold, :ts
                    )
                    ON CONFLICT (trade_id, factor_name, factor_tf)
                    DO NOTHING
                    """
                ),
                {
                    "tid": trade_id, "uid": user_id, "symbol": symbol,
                    "timeframe": timeframe,
                    "fname": tag, "rl": regime_label,
                    "r": r_multiple, "outcome": outcome, "hold": is_holdout,
                    "ts": closed_at,
                },
            )
            if result.rowcount:
                rows_inserted += int(result.rowcount)

    return rows_inserted


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
    holdout_pct: int = DEFAULT_HOLDOUT_PCT,
) -> None:
    """Cambia status y registra setup_event en la misma transacción.

    Cuando `new_status='closed'` (F5.5):
    - Computa `is_holdout` determinista vía hash y persiste la marca.
    - Fan-out de `factor_snapshot` a `factor_outcomes` (idempotente).
    El fan-out es no-op si el trade no tiene factor_snapshot (filas pre-F5.5
    o setups que no provinieron de TradeIdea con scoring).
    """
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
    # From-status inferred from the event: entry_hit always comes from
    # pending; sl/tp/manual_close from active. The metric helps detect
    # weird transition flows (e.g., entry_hit from cancelled would be a bug).
    from_status = (
        "pending" if event == "entry_hit" else "active"
    )
    setup_transitions_total.labels(
        from_status=from_status, to_status=new_status, event=event
    ).inc()

    # --- F5.5: holdout split + fan-out a factor_outcomes ---------------------
    if new_status == "closed" and r_multiple is not None:
        # Necesitamos user_id, symbol, timeframe, factor_snapshot para fan-out.
        row = (
            await session.execute(
                text(
                    """
                    SELECT user_id, symbol, timeframe, factor_snapshot, targets
                    FROM journal_trades
                    WHERE id = CAST(:tid AS uuid)
                    """
                ),
                {"tid": trade_id},
            )
        ).mappings().one_or_none()
        if row is None:
            return

        is_holdout = compute_is_holdout(
            trade_id=trade_id, user_id=row["user_id"], holdout_pct=holdout_pct,
        )
        await session.execute(
            text(
                """
                UPDATE journal_trades
                SET is_holdout = :hold
                WHERE id = CAST(:tid AS uuid)
                """
            ),
            {"tid": trade_id, "hold": is_holdout},
        )

        # Fan-out solo si el trade tiene factor_snapshot persistido. Sin él,
        # no podemos atribuir el outcome a factores específicos — el trade
        # cuenta para WR global pero no contribuye a factor stats.
        snapshot_raw = row.get("factor_snapshot")
        snapshot: dict[str, Any] | None = None
        if isinstance(snapshot_raw, dict):
            snapshot = snapshot_raw
        elif isinstance(snapshot_raw, str):
            try:
                snapshot = json.loads(snapshot_raw)
            except Exception:
                snapshot = None

        if snapshot:
            # Detectar si TODOS los TPs fueron tocados (partial_win vs win).
            targets_raw = row.get("targets") or []
            if isinstance(targets_raw, str):
                try:
                    targets_raw = json.loads(targets_raw)
                except Exception:
                    targets_raw = []
            all_tps_hit = bool(targets_raw) and all(
                isinstance(t, dict) and t.get("hit_at") is not None
                for t in targets_raw
            )
            outcome = _classify_outcome(
                r_multiple=r_multiple,
                exit_reason=("tp_hit" if event == "tp_hit"
                             else "sl_hit" if event == "sl_hit"
                             else "manual_close"),
                all_tps_hit=all_tps_hit,
            )
            await _fanout_factor_outcomes(
                session,
                trade_id=trade_id,
                user_id=row["user_id"],
                symbol=row["symbol"],
                timeframe=row["timeframe"],
                factor_snapshot=snapshot,
                r_multiple=float(r_multiple),
                outcome=outcome,
                is_holdout=is_holdout,
                closed_at=candle_ts,
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


async def transition_to_invalidated(
    session: AsyncSession,
    *,
    trade_id: str,
    candle_ts: datetime,
    payload: dict[str, Any],
) -> bool:
    """Auto-invalidation trigger fired (condition matched OR expires_at passed).

    Moves the setup from `pending` to terminal `cancelled` and writes a
    `setup_events` row with event=`invalidated` (distinct from the manual
    `cancelled` event so the journal can attribute auto-cancels separately).
    Idempotent via the status='pending' guard — duplicate fires no-op.
    Returns True if the row was modified.

    Audit fix 2026-05: `SELECT ... FOR UPDATE` lockea la fila antes del
    UPDATE, simétrico al patrón de `_evaluate_setup` en entry_hit. Sin
    esto, dos paths concurrentes (sweeper expiry + condition match) pueden
    ambos hacer UPDATE — Postgres serializa el segundo, que ve status≠'pending'
    y rebota — pero el SELECT FOR UPDATE hace el contrato explícito y
    previene regresiones futuras si alguien añade otra ruta.
    """
    locked_status = (
        await session.execute(
            text(
                "SELECT status FROM journal_trades "
                "WHERE id = CAST(:tid AS uuid) FOR UPDATE"
            ),
            {"tid": trade_id},
        )
    ).scalar_one_or_none()
    if locked_status != "pending":
        return False
    result = await session.execute(
        text(
            """
            UPDATE journal_trades
            SET status = 'cancelled',
                invalidated_at = :ts,
                closed_at = :ts,
                updated_at = now()
            WHERE id = CAST(:tid AS uuid) AND status = 'pending'
            """
        ),
        {"tid": trade_id, "ts": candle_ts},
    )
    if result.rowcount == 0:
        return False
    await session.execute(
        text(
            """
            INSERT INTO setup_events (trade_id, event, candle_ts, payload)
            VALUES (CAST(:tid AS uuid), 'invalidated', :ts, CAST(:payload AS jsonb))
            """
        ),
        {
            "tid": trade_id,
            "ts": candle_ts,
            "payload": json.dumps(payload),
        },
    )
    setup_transitions_total.labels(
        from_status="pending", to_status="cancelled", event="invalidated"
    ).inc()
    return True


async def list_pending_with_expiry(
    session: AsyncSession,
) -> list[dict[str, Any]]:
    """Subset for the wall-clock expiry sweep: pending setups with a non-null
    `expires_at`. Used by the SetupRuntime sweeper (separate from the per-
    candle evaluator) so slow-TF setups (4h/1d) don't have their expiry
    delayed by hours of waiting for the next candle close.
    """
    rows = (
        await session.execute(
            text(
                """
                SELECT id::text, symbol, timeframe, side, status, expires_at
                FROM journal_trades
                WHERE status = 'pending'
                  AND source IN ('agent_proposal', 'scout_proposal')
                  AND expires_at IS NOT NULL
                """
            )
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def has_approval_event(
    session: AsyncSession, *, trade_id: str
) -> bool:
    """Returns True if `setup_events` has an `approved` row for this trade.

    Used by SetupRuntime to gate the pending → active transition for
    `scout_proposal` setups: scout proposals must be explicitly approved by
    the user (Telegram button, web push action, or REST endpoint) before
    they're allowed to track entry hits. Chat-initiated `agent_proposal`
    setups bypass this check (user already asked for them in real time).
    """
    row = (
        await session.execute(
            text(
                """
                SELECT 1
                FROM setup_events
                WHERE trade_id = CAST(:tid AS uuid) AND event = 'approved'
                LIMIT 1
                """
            ),
            {"tid": trade_id},
        )
    ).scalar_one_or_none()
    return row is not None


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
            "entry_px", "stop_loss_px", "exit_px", "size", "r_multiple",
        } and v is not None:
            out[k] = float(v)
        else:
            out[k] = v
    for jsonb_key in ("targets", "invalidation_conditions", "expires_at_citations"):
        if isinstance(out.get(jsonb_key), str):
            try:
                out[jsonb_key] = json.loads(out[jsonb_key])
            except Exception:
                out[jsonb_key] = [] if jsonb_key != "expires_at_citations" else None
    if out.get("targets") is None:
        out["targets"] = []
    if out.get("invalidation_conditions") is None:
        out["invalidation_conditions"] = []
    return out
