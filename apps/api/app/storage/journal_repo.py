"""Repository for the trade journal — raw SQL because the `vector(1024)` column
needs pgvector-aware bindings that the SQLAlchemy ORM adapter doesn't give us
out of the box. Async session.execute(text(...)) keeps it simple.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel
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
    summary_text: str
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
) -> None:
    await session.execute(
        text(
            """
            UPDATE journal_trades
            SET summary_text = :s, summary_hash = :h,
                embedding = CAST(:emb AS vector),
                embedding_version = :ver,
                updated_at = now()
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {
            "s": summary_text,
            "h": summary_hash,
            "emb": _vector_literal(embedding),
            "ver": embedding_version,
            "id": trade_id,
        },
    )


# -----------------------------------------------------------------------------
# Queries
# -----------------------------------------------------------------------------


async def get_by_id(session: AsyncSession, trade_id: str) -> JournalTradeRow | None:
    row = (
        await session.execute(
            text(
                """
                SELECT id, user_id, trade_ts, symbol, timeframe, mode, side,
                       entry_px, exit_px, size, r_multiple, setup_tag, regime, mistakes,
                       summary_text, summary_hash, embedding_version
                FROM journal_trades WHERE id = CAST(:id AS uuid)
                """
            ),
            {"id": trade_id},
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


async def list_all_for_embed_check(
    session: AsyncSession, *, batch_size: int = 100
) -> Sequence[JournalTradeRow]:
    """Trades that may need re-embedding. Caller (embed_backfill.py) computes
    the canonical summary in Python and compares against `summary_hash` to
    detect drift — we don't push the hash check into SQL because pgcrypto
    isn't an assumed extension and the row count here is small.
    """
    rows = (
        await session.execute(
            text(
                """
                SELECT id, user_id, trade_ts, symbol, timeframe, mode, side,
                       entry_px, exit_px, size, r_multiple, setup_tag, regime, mistakes,
                       summary_text, summary_hash, embedding_version
                FROM journal_trades
                ORDER BY trade_ts ASC
                LIMIT :lim
                """
            ),
            {"lim": batch_size},
        )
    ).mappings().all()
    return [JournalTradeRow.model_validate(dict(r)) for r in rows]


# -----------------------------------------------------------------------------
# Hybrid search — Reciprocal Rank Fusion (BM25 via tsvector + dense via pgvector)
# -----------------------------------------------------------------------------


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
    SELECT t.id, t.trade_ts, t.symbol, t.timeframe, t.side, t.setup_tag,
           t.regime, t.r_multiple, t.summary_text, f.rrf AS rrf_score
    FROM journal_trades t
    JOIN fused f USING (id)
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
    return [JournalSearchHit.model_validate(dict(r)) for r in rows]


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
