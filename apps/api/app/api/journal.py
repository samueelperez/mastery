"""REST endpoints para el journal de trades + setups con lifecycle.

Legacy:
- GET /journal/trades — lista filtrable (compat con `/research/journal`).
- GET /journal/trades/{id} — detail.

Nuevo (PR1):
- GET /journal/setups — setups propuestos por el agente con status.
- GET /journal/setups/{id} — detail con setup_events timeline.
- POST /journal/setups/{id}/cancel — soft-cancel de un setup pending.
- GET /strategies/winrate — agregados por setup_tag (closed only).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_user_id
from app.db import session_dependency
from app.storage.setup_repo import (
    cancel_setup,
    count_setups_by_status,
    get_setup_with_events,
    list_setups,
    winrate_by_setup_tag,
)

router = APIRouter()

JournalMode = Literal["paper", "live", "manual_log", "csv_import"]
SetupStatusLit = Literal["pending", "active", "closed", "cancelled"]


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
    user_id: Annotated[str, Depends(require_user_id)],
    mode: Annotated[JournalMode | None, Query()] = None,
    regime: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[JournalTradeListRow]:
    sql = """
        SELECT id::text, trade_ts, symbol, timeframe, mode, side,
               entry_px, exit_px, size, r_multiple, setup_tag, regime, mistakes
        FROM journal_trades
        WHERE user_id = :uid
    """
    params: dict[str, Any] = {"uid": user_id, "lim": limit, "off": offset}
    if mode is not None:
        sql += " AND mode = :mode"
        params["mode"] = mode
    if regime is not None:
        sql += " AND regime = :regime"
        params["regime"] = regime
    sql += " ORDER BY trade_ts DESC LIMIT :lim OFFSET :off"
    rows = (await session.execute(text(sql), params)).mappings().all()
    return [JournalTradeListRow(**dict(r)) for r in rows]


@router.get(
    "/journal/trades/{trade_id}", response_model=JournalTradeDetail, tags=["research"]
)
async def get_journal_trade(
    trade_id: str,
    session: Annotated[AsyncSession, Depends(session_dependency)],
    user_id: Annotated[str, Depends(require_user_id)],
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
                WHERE id = CAST(:tid AS uuid) AND user_id = :uid
                """
            ),
            {"tid": trade_id, "uid": user_id},
        )
    ).mappings().one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail=f"journal_trade {trade_id} not found")
    return JournalTradeDetail(**dict(row))


# ---------------------------------------------------------------------------
# Setups (lifecycle pending → active → closed)
# ---------------------------------------------------------------------------


class SetupTargetDTO(BaseModel):
    label: str
    price: float
    rationale: str | None = None
    hit_at: datetime | None = None


class SetupListRow(BaseModel):
    id: str
    user_id: str
    trade_ts: datetime
    symbol: str
    timeframe: str
    side: str
    status: str
    source: str
    entry_px: float
    invalidation_px: float | None
    exit_px: float | None
    size: float
    r_multiple: float | None
    setup_tag: str
    regime: str
    confidence: str | None
    targets: list[SetupTargetDTO]
    mistakes: str | None
    proposed_at: datetime | None
    entry_hit_at: datetime | None
    closed_at: datetime | None
    created_at: datetime


class SetupEventRow(BaseModel):
    id: str
    event: str
    candle_ts: datetime
    payload: dict[str, Any]
    created_at: datetime


class SetupDetail(SetupListRow):
    summary_text: str
    news_24h: dict[str, Any]
    features: dict[str, Any]
    mistakes: str | None
    updated_at: datetime
    events: list[SetupEventRow]


class StatusCounts(BaseModel):
    pending: int
    active: int
    closed: int
    cancelled: int


class SetupListResponse(BaseModel):
    rows: list[SetupListRow]
    counts: StatusCounts


@router.get(
    "/journal/setups",
    response_model=SetupListResponse,
    tags=["journal"],
)
async def list_journal_setups(
    session: Annotated[AsyncSession, Depends(session_dependency)],
    user_id: Annotated[str, Depends(require_user_id)],
    status: Annotated[SetupStatusLit | None, Query()] = None,
    symbol: Annotated[str | None, Query()] = None,
    source: Annotated[str | None, Query()] = "agent_proposal",
    setup_tag: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SetupListResponse:
    rows_raw = await list_setups(
        session,
        user_id=user_id,
        status=status,
        symbol=symbol,
        source=source,
        setup_tag=setup_tag,
        limit=limit,
        offset=offset,
    )
    counts = await count_setups_by_status(
        session, user_id=user_id, symbol=symbol
    )
    return SetupListResponse(
        rows=[SetupListRow(**r) for r in rows_raw],
        counts=StatusCounts(**counts),
    )


@router.get(
    "/journal/setups/{trade_id}",
    response_model=SetupDetail,
    tags=["journal"],
)
async def get_journal_setup(
    trade_id: str,
    session: Annotated[AsyncSession, Depends(session_dependency)],
    user_id: Annotated[str, Depends(require_user_id)],
) -> SetupDetail:
    row = await get_setup_with_events(
        session, user_id=user_id, trade_id=trade_id
    )
    if not row:
        raise HTTPException(
            status_code=404, detail=f"setup {trade_id} not found"
        )
    return SetupDetail(**row)


@router.post(
    "/journal/setups/{trade_id}/cancel",
    response_model=dict,
    tags=["journal"],
)
async def cancel_journal_setup(
    trade_id: str,
    session: Annotated[AsyncSession, Depends(session_dependency)],
    user_id: Annotated[str, Depends(require_user_id)],
) -> dict[str, Any]:
    ok = await cancel_setup(session, user_id=user_id, trade_id=trade_id)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail=(
                "setup not cancellable (must exist, belong to user, "
                "and have status='pending')"
            ),
        )
    return {"status": "cancelled"}


# ---------------------------------------------------------------------------
# Strategies / winrate (PR1 backend, PR2 frontend)
# ---------------------------------------------------------------------------


class StrategyWinrateRow(BaseModel):
    setup_tag: str
    n_closed: int
    n_wins: int
    win_rate_pct: float | None
    avg_r: float | None
    last_closed_at: datetime | None


@router.get(
    "/strategies/winrate",
    response_model=list[StrategyWinrateRow],
    tags=["research", "journal"],
)
async def list_strategy_winrate(
    session: Annotated[AsyncSession, Depends(session_dependency)],
    user_id: Annotated[str, Depends(require_user_id)],
    min_n: Annotated[int, Query(ge=1, le=100)] = 1,
) -> list[StrategyWinrateRow]:
    rows = await winrate_by_setup_tag(session, user_id=user_id, min_n=min_n)
    return [StrategyWinrateRow(**r) for r in rows]
