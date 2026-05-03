"""Repository over the `ohlcv` hypertable.

Uses `INSERT ... ON CONFLICT DO NOTHING` so backfill and live ingestion can
re-write overlapping ranges idempotently. The hypertable's PK
(exchange, symbol, timeframe, ts) is the conflict target.
"""

from collections.abc import Iterable, Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.types import OHLCVCandle
from app.storage.models import OHLCV


async def bulk_upsert(session: AsyncSession, candles: Iterable[OHLCVCandle]) -> int:
    rows = [
        {
            "exchange": c.exchange,
            "symbol": c.symbol,
            "timeframe": c.timeframe,
            "ts": c.ts,
            "o": c.o,
            "h": c.h,
            "l": c.l,
            "c": c.c,
            "v": c.v,
        }
        for c in candles
    ]
    if not rows:
        return 0

    stmt = insert(OHLCV).values(rows).on_conflict_do_nothing(
        index_elements=["exchange", "symbol", "timeframe", "ts"]
    )
    result = await session.execute(stmt)
    return result.rowcount or 0


async def upsert_one(session: AsyncSession, candle: OHLCVCandle) -> bool:
    """Returns True if the row was newly inserted, False if it already existed.

    Used by live ingestion when a candle closes — we only persist closed
    candles so backfill remains the source of truth for historical data.
    """
    stmt = insert(OHLCV).values(
        exchange=candle.exchange,
        symbol=candle.symbol,
        timeframe=candle.timeframe,
        ts=candle.ts,
        o=candle.o,
        h=candle.h,
        l=candle.l,
        c=candle.c,
        v=candle.v,
    ).on_conflict_do_nothing(
        index_elements=["exchange", "symbol", "timeframe", "ts"]
    )
    result = await session.execute(stmt)
    return (result.rowcount or 0) == 1


async def fetch_range(
    session: AsyncSession,
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 1000,
) -> Sequence[OHLCV]:
    stmt = (
        select(OHLCV)
        .where(
            OHLCV.exchange == exchange,
            OHLCV.symbol == symbol,
            OHLCV.timeframe == timeframe,
        )
        .order_by(OHLCV.ts.desc())
        .limit(limit)
    )
    if since is not None:
        stmt = stmt.where(OHLCV.ts >= since)
    if until is not None:
        stmt = stmt.where(OHLCV.ts <= until)
    result = await session.execute(stmt)
    rows = result.scalars().all()
    # Caller wants oldest-first for charting.
    return list(reversed(rows))


async def count_rows(
    session: AsyncSession,
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
) -> int:
    from sqlalchemy import func

    stmt = select(func.count()).select_from(OHLCV).where(
        OHLCV.exchange == exchange,
        OHLCV.symbol == symbol,
        OHLCV.timeframe == timeframe,
    )
    return int((await session.execute(stmt)).scalar_one())
