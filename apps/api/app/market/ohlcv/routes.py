"""Historical OHLCV REST endpoint.

GET /ohlcv/{symbol}/{timeframe}?since=...&until=...&limit=1000
Returns candles oldest-first, ready for chart consumption.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import session_dependency
from app.core.exchanges.binance_adapter import EXCHANGE_NAME
from app.market.ohlcv.repo import fetch_range

router = APIRouter()


class CandleOut(BaseModel):
    ts: datetime
    o: float
    h: float
    l: float
    c: float
    v: float


class OHLCVResponse(BaseModel):
    exchange: str
    symbol: str
    timeframe: str
    count: int
    candles: list[CandleOut]


@router.get("/ohlcv/{symbol}/{timeframe}", response_model=OHLCVResponse, tags=["market"])
async def get_ohlcv(
    symbol: str,
    timeframe: str,
    session: Annotated[AsyncSession, Depends(session_dependency)],
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=10_000)] = 1000,
) -> OHLCVResponse:
    rows = await fetch_range(
        session,
        exchange=EXCHANGE_NAME,
        symbol=symbol.upper(),
        timeframe=timeframe,
        since=since,
        until=until,
        limit=limit,
    )
    return OHLCVResponse(
        exchange=EXCHANGE_NAME,
        symbol=symbol.upper(),
        timeframe=timeframe,
        count=len(rows),
        candles=[CandleOut(ts=r.ts, o=r.o, h=r.h, l=r.l, c=r.c, v=r.v) for r in rows],
    )
