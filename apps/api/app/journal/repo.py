"""Repository for the trade journal — raw SQL because the `vector(1024)` column
needs pgvector-aware bindings that the SQLAlchemy ORM adapter doesn't give us
out of the box. Async session.execute(text(...)) keeps it simple.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

JournalMode = Literal["paper", "live", "manual_log", "csv_import"]
TradeSide = Literal["long", "short"]


class JournalTradeIn(BaseModel):
    """Input shape for inserting a trade. `embedding` is set lazily by the
    embed_backfill / log_trade tool so the same insert path works whether or
    not the Voyage API is reachable at write time."""

    user_id: str = "me"
    trade_ts: datetime
    symbol: str
    timeframe: str
    mode: JournalMode
    side: TradeSide
    entry_px: float
    exit_px: float | None = None
    size: float
    r_multiple: float | None = None
    setup_tag: str
    regime: str
    mistakes: str | None = None
    news_24h: dict[str, Any] | None = None
    features: dict[str, Any] | None = None
    # 300-char truncation invariant (CLAUDE.md). Sin esto el listing
    # devuelve summaries cuyo render rompe layout (~600 chars en
    # casos reales). Audit fix 2026-05.
    summary_text: str = Field(..., max_length=300)
    summary_hash: str
    embedding: list[float] | None = None
    embedding_version: int = 1


class JournalTradeRow(BaseModel):
    id: str
    user_id: str
    trade_ts: datetime
    symbol: str
    timeframe: str
    mode: str
    side: str
    entry_px: float
    exit_px: float | None
    size: float
    r_multiple: float | None
    setup_tag: str
    regime: str
    mistakes: str | None
    summary_text: str
    summary_hash: str
    embedding_version: int


# -----------------------------------------------------------------------------
# Insert / update
# -----------------------------------------------------------------------------


def _vector_literal(v: list[float] | None) -> str | None:
    """pgvector accepts text representation '[0.1,0.2,...]'; we cast it server-side."""
    if v is None:
        return None
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


_INSERT_SQL = text(
    """
    INSERT INTO journal_trades (
        user_id, trade_ts, symbol, timeframe, mode, side,
        entry_px, exit_px, size, r_multiple, setup_tag, regime, mistakes,
        news_24h, features, summary_text, summary_hash,
        embedding, embedding_version
    ) VALUES (
        :user_id, :trade_ts, :symbol, :timeframe, :mode, :side,
        :entry_px, :exit_px, :size, :r_multiple, :setup_tag, :regime, :mistakes,
        CAST(:news_24h AS jsonb), CAST(:features AS jsonb),
        :summary_text, :summary_hash,
        CAST(:embedding AS vector), :embedding_version
    )
    RETURNING id
    """
)


async def insert_trade(session: AsyncSession, trade: JournalTradeIn) -> str:
    result = await session.execute(
        _INSERT_SQL,
        {
            "user_id": trade.user_id,
            "trade_ts": trade.trade_ts,
            "symbol": trade.symbol,
            "timeframe": trade.timeframe,
            "mode": trade.mode,
            "side": trade.side,
            "entry_px": trade.entry_px,
            "exit_px": trade.exit_px,
            "size": trade.size,
            "r_multiple": trade.r_multiple,
            "setup_tag": trade.setup_tag,
            "regime": trade.regime,
            "mistakes": trade.mistakes,
            "news_24h": json.dumps(trade.news_24h or {}),
            "features": json.dumps(trade.features or {}),
            "summary_text": trade.summary_text,
            "summary_hash": trade.summary_hash,
            "embedding": _vector_literal(trade.embedding),
            "embedding_version": trade.embedding_version,
        },
    )
    return str(result.scalar_one())


async def bulk_insert(session: AsyncSession, trades: Iterable[JournalTradeIn]) -> int:
    n = 0
    for t in trades:
        await insert_trade(session, t)
        n += 1
    return n


async def update_summary_and_embedding(
    session: AsyncSession,
    *,
    trade_id: str,
    summary_text: str,
    summary_hash: str,
    embedding: list[float],
    embedding_version: int,
    expected_old_hash: str | None = None,
) -> bool:
    """Compare-and-swap update of a trade's embedded summary.

    `expected_old_hash` is the summary_hash that was current when we computed
    `embedding`. If the row has since changed (user edited mistakes, another
    backfill ran), the UPDATE matches 0 rows and we return False — caller
    should re-read and retry. Without this, embed_backfill can race against
    log_trade / live edits and pin a stale embedding to a fresh summary_text.
    """
    result = await session.execute(
        text(
            """
            UPDATE journal_trades
            SET summary_text = :s, summary_hash = :h,
                embedding = CAST(:emb AS vector),
                embedding_version = :ver,
                updated_at = now()
            WHERE id = CAST(:id AS uuid)
              AND (:expected IS NULL OR summary_hash = :expected)
            """
        ),
        {
            "s": summary_text,
            "h": summary_hash,
            "emb": _vector_literal(embedding),
            "ver": embedding_version,
            "id": trade_id,
            "expected": expected_old_hash,
        },
    )
    return (getattr(result, "rowcount", 0) or 0) > 0


# -----------------------------------------------------------------------------
# Queries
# -----------------------------------------------------------------------------


async def get_by_id(
    session: AsyncSession, trade_id: str, *, user_id: str
) -> JournalTradeRow | None:
    """Lee un trade por id. `user_id` es obligatorio — sin scoping un user
    podía leer trades de otro si conocía el UUID (audit fix 2026-05)."""
    row = (
        await session.execute(
            text(
                """
                SELECT id, user_id, trade_ts, symbol, timeframe, mode, side,
                       entry_px, exit_px, size, r_multiple, setup_tag, regime, mistakes,
                       summary_text, summary_hash, embedding_version
                FROM journal_trades
                WHERE id = CAST(:id AS uuid) AND user_id = :uid
                """
            ),
            {"id": trade_id, "uid": user_id},
        )
    ).mappings().one_or_none()
    return JournalTradeRow.model_validate(dict(row)) if row else None


async def list_recent(
    session: AsyncSession,
    *,
    user_id: str = "me",
    mode: JournalMode | None = None,
    regime: str | None = None,
    limit: int = 50,
) -> Sequence[JournalTradeRow]:
    sql = """
        SELECT id, user_id, trade_ts, symbol, timeframe, mode, side,
               entry_px, exit_px, size, r_multiple, setup_tag, regime, mistakes,
               summary_text, summary_hash, embedding_version
        FROM journal_trades
        WHERE user_id = :uid
    """
    params: dict[str, Any] = {"uid": user_id, "lim": limit}
    if mode is not None:
        sql += " AND mode = :mode"
        params["mode"] = mode
    if regime is not None:
        sql += " AND regime = :regime"
        params["regime"] = regime
    sql += " ORDER BY trade_ts DESC LIMIT :lim"
    rows = (await session.execute(text(sql), params)).mappings().all()
    return [JournalTradeRow.model_validate(dict(r)) for r in rows]


async def list_users_with_trades(session: AsyncSession) -> Sequence[str]:
    """Returns distinct user_ids that own journal_trades. Used by
    embed_backfill to iterate per-user (audit fix 2026-05)."""
    rows = (
        await session.execute(
            text("SELECT DISTINCT user_id FROM journal_trades ORDER BY user_id")
        )
    ).scalars().all()
    return list(rows)


async def list_all_for_embed_check(
    session: AsyncSession, *, user_id: str, batch_size: int = 100
) -> Sequence[JournalTradeRow]:
    """Trades del user que pueden necesitar re-embedding. El caller
    (embed_backfill.py) computa el summary canónico en Python y lo compara
    con `summary_hash`. `user_id` es obligatorio — antes mezclaba trades
    cross-user en el re-embed (audit fix 2026-05).
    """
    rows = (
        await session.execute(
            text(
                """
                SELECT id, user_id, trade_ts, symbol, timeframe, mode, side,
                       entry_px, exit_px, size, r_multiple, setup_tag, regime, mistakes,
                       summary_text, summary_hash, embedding_version
                FROM journal_trades
                WHERE user_id = :uid
                ORDER BY trade_ts ASC
                LIMIT :lim
                """
            ),
            {"uid": user_id, "lim": batch_size},
        )
    ).mappings().all()
    return [JournalTradeRow.model_validate(dict(r)) for r in rows]


# -----------------------------------------------------------------------------
# Hybrid search — Reciprocal Rank Fusion (BM25 via tsvector + dense via pgvector)
# -----------------------------------------------------------------------------


class PostMortemHitInfo(BaseModel):
    """Compact view del post-mortem adjuntado a un trade similar.

    Solo presente cuando el trade tiene un post-mortem persistido. Mantenemos
    el shape mínimo útil para que el agente principal entienda QUÉ se aprendió
    del trade análogo: veredicto, lección, factores que fallaron/funcionaron y
    calibración de confianza.
    """

    verdict: str  # thesis_held | thesis_broken | execution_error | noise
    lesson_es: str
    failure_factors: list[str]
    success_factors: list[str]
    confidence_calibration: str  # over | under | calibrated


class JournalSearchHit(BaseModel):
    id: str
    trade_ts: datetime
    symbol: str
    timeframe: str
    side: str
    setup_tag: str
    regime: str
    r_multiple: float | None
    summary_text: str
    rrf_score: float
    # F5.5: cuando el trade tiene post-mortem, lo adjuntamos para que el
    # agente vea no solo "qué pasó" (r_multiple, summary) sino "qué se
    # aprendió" (verdict, lección, factores). LEFT JOIN — None si no existe.
    post_mortem: PostMortemHitInfo | None = None


_HYBRID_SQL = text(
    """
    WITH dense AS (
      SELECT id, ROW_NUMBER() OVER (ORDER BY embedding <=> CAST(:q_vec AS vector)) AS rank
      FROM journal_trades
      WHERE user_id = :uid AND embedding IS NOT NULL
      ORDER BY embedding <=> CAST(:q_vec AS vector)
      LIMIT 50
    ),
    bm25 AS (
      SELECT id,
             ROW_NUMBER() OVER (ORDER BY ts_rank(tsv, plainto_tsquery('english', :q_text)) DESC) AS rank
      FROM journal_trades
      WHERE user_id = :uid
        AND :q_text <> ''
        AND tsv @@ plainto_tsquery('english', :q_text)
      ORDER BY ts_rank(tsv, plainto_tsquery('english', :q_text)) DESC
      LIMIT 50
    ),
    fused AS (
      SELECT id, SUM(score) AS rrf
      FROM (
        SELECT id, 1.0/(60 + rank) AS score FROM dense
        UNION ALL
        SELECT id, 1.0/(60 + rank) AS score FROM bm25
      ) u
      GROUP BY id
      ORDER BY rrf DESC
      LIMIT :k
    )
    -- Cast id::text porque la columna es `uuid` y asyncpg lo devuelve como
    -- objeto Python uuid.UUID. Pydantic v2 no coerce a str automáticamente
    -- y JournalSearchHit.id está declarado `str` → ValidationError.
    --
    -- F5.5: LEFT JOIN setup_post_mortems para que cada hit lleve la wisdom
    -- aprendida tras cerrar (NULL si el trade no tuvo post-mortem o aún no
    -- se ejecutó). Las listas failure_factors/success_factors no son
    -- columnas; viven dentro de factor_verdicts JSONB. El cliente Python
    -- las deriva filtrando por verdict ∈ {'failed','worked'} en _hit_from_row.
    -- Defense-in-depth: el JOIN exige pm.user_id = :uid aunque en condiciones
    -- normales pm.trade_id → t.id ya lo garantiza vía FK.
    SELECT t.id::text AS id, t.trade_ts, t.symbol, t.timeframe, t.side, t.setup_tag,
           t.regime, t.r_multiple, t.summary_text, f.rrf AS rrf_score,
           pm.verdict AS pm_verdict,
           pm.lesson_es AS pm_lesson_es,
           pm.factor_verdicts AS pm_factor_verdicts,
           pm.confidence_calibration AS pm_confidence_calibration
    FROM journal_trades t
    JOIN fused f USING (id)
    LEFT JOIN setup_post_mortems pm
      ON pm.trade_id = t.id AND pm.user_id = :uid
    ORDER BY f.rrf DESC
    """
)


async def hybrid_search(
    session: AsyncSession,
    *,
    query_text: str,
    query_embedding: list[float],
    k: int = 5,
    user_id: str = "me",
) -> list[JournalSearchHit]:
    rows = (
        await session.execute(
            _HYBRID_SQL,
            {
                "uid": user_id,
                "q_text": query_text or "",
                "q_vec": _vector_literal(query_embedding),
                "k": k,
            },
        )
    ).mappings().all()
    return [_hit_from_row(dict(r)) for r in rows]


def _hit_from_row(row: dict[str, Any]) -> JournalSearchHit:
    """Construye `JournalSearchHit` desde una row de `_HYBRID_SQL`. Si las
    columnas `pm_*` están pobladas, adjunta el `post_mortem`; si están
    NULL (trade sin post-mortem persistido), `post_mortem=None`.

    `factor_verdicts` (JSONB con shape `{factor_key: {verdict, ...}}`) se
    filtra a las listas `failure_factors` / `success_factors` (keys cuyo
    verdict es 'failed' / 'worked'). Las keys con verdict 'neutral' o
    desconocido se omiten."""
    pm_info: PostMortemHitInfo | None = None
    if row.get("pm_verdict") is not None and row.get("pm_lesson_es") is not None:
        # `factor_verdicts` viene como dict ya parseado en asyncpg moderno;
        # si llega como str (driver legacy) parseamos defensivamente.
        raw_fv = row.get("pm_factor_verdicts")
        if isinstance(raw_fv, str):
            try:
                raw_fv = json.loads(raw_fv)
            except Exception:
                raw_fv = None
        failure_factors: list[str] = []
        success_factors: list[str] = []
        if isinstance(raw_fv, dict):
            for key, info in raw_fv.items():
                if not isinstance(info, dict):
                    continue
                verdict = info.get("verdict")
                if verdict == "failed":
                    failure_factors.append(str(key))
                elif verdict == "worked":
                    success_factors.append(str(key))

        pm_info = PostMortemHitInfo(
            verdict=str(row["pm_verdict"]),
            lesson_es=str(row["pm_lesson_es"]),
            failure_factors=failure_factors,
            success_factors=success_factors,
            confidence_calibration=str(row.get("pm_confidence_calibration") or "calibrated"),
        )

    return JournalSearchHit(
        id=row["id"],
        trade_ts=row["trade_ts"],
        symbol=row["symbol"],
        timeframe=row["timeframe"],
        side=row["side"],
        setup_tag=row["setup_tag"],
        regime=row["regime"],
        r_multiple=row["r_multiple"],
        summary_text=row["summary_text"],
        rrf_score=row["rrf_score"],
        post_mortem=pm_info,
    )


# -----------------------------------------------------------------------------
# Bias events readout
# -----------------------------------------------------------------------------


class BiasEventRow(BaseModel):
    id: int
    detected_at: datetime
    kind: str
    severity: str
    payload: dict[str, Any]
    window_start: datetime
    window_end: datetime


async def list_recent_bias_events(
    session: AsyncSession, *, user_id: str = "me", limit: int = 20
) -> list[BiasEventRow]:
    rows = (
        await session.execute(
            text(
                """
                SELECT id, detected_at, kind, severity, payload, window_start, window_end
                FROM bias_events
                WHERE user_id = :uid
                ORDER BY detected_at DESC
                LIMIT :lim
                """
            ),
            {"uid": user_id, "lim": limit},
        )
    ).mappings().all()
    return [BiasEventRow.model_validate(dict(r)) for r in rows]
