"""Read-only REST endpoints for the /research/journal surface.

GET /journal/trades — list (filterable by mode/regime).
GET /journal/trades/{id} — full detail (post-mortem text + features).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_dependency

router = APIRouter()

JournalMode = Literal["paper", "live", "manual_log", "csv_import"]


class JournalTradeListRow(BaseModel):
    id: str
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
    mistakes: str | None  # may be long; UI truncates


class JournalTradeDetail(JournalTradeListRow):
    summary_text: str
    summary_hash: str
    embedding_version: int
    news_24h: dict[str, Any]
    features: dict[str, Any]


@router.get("/journal/trades", response_model=list[JournalTradeListRow], tags=["research"])
async def list_journal_trades(
    session: Annotated[AsyncSession, Depends(session_dependency)],
    user_id: Annotated[str, Query()] = "me",
    mode: Annotated[JournalMode | None, Query()] = None,
    regime: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[JournalTradeListRow]:
    sql = """
        SELECT id::text, trade_ts, symbol, timeframe, mode, side,
               entry_px, exit_px, size, r_multiple, setup_tag, regime, mistakes
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
    return [JournalTradeListRow(**dict(r)) for r in rows]


@router.get(
    "/journal/trades/{trade_id}", response_model=JournalTradeDetail, tags=["research"]
)
async def get_journal_trade(
    trade_id: str,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> JournalTradeDetail:
    row = (
        await session.execute(
            text(
                """
                SELECT id::text, trade_ts, symbol, timeframe, mode, side,
                       entry_px, exit_px, size, r_multiple, setup_tag, regime, mistakes,
                       summary_text, summary_hash, embedding_version,
                       news_24h, features
                FROM journal_trades
                WHERE id = CAST(:tid AS uuid)
                """
            ),
            {"tid": trade_id},
        )
    ).mappings().one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail=f"journal_trade {trade_id} not found")
    return JournalTradeDetail(**dict(row))
