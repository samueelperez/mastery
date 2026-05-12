"""Repository over the `ohlcv` hypertable.

Uses `INSERT ... ON CONFLICT DO NOTHING` so backfill and live ingestion can
re-write overlapping ranges idempotently. The hypertable's PK
(exchange, symbol, timeframe, ts) is the conflict target.
"""

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools._time import floor_to_timeframe
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

    stmt = (
        insert(OHLCV)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["exchange", "symbol", "timeframe", "ts"])
    )
    result = await session.execute(stmt)
    # rowcount lives on CursorResult (sync) but mypy sees the async base type;
    # at runtime asyncpg backs this with a real CursorResult.
    return getattr(result, "rowcount", 0) or 0


async def upsert_one(session: AsyncSession, candle: OHLCVCandle) -> bool:
    """Returns True if the row was newly inserted, False if it already existed.

    Used by live ingestion when a candle closes — we only persist closed
    candles so backfill remains the source of truth for historical data.
    """
    stmt = (
        insert(OHLCV)
        .values(
            exchange=candle.exchange,
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            ts=candle.ts,
            o=candle.o,
            h=candle.h,
            l=candle.l,
            c=candle.c,
            v=candle.v,
        )
        .on_conflict_do_nothing(index_elements=["exchange", "symbol", "timeframe", "ts"])
    )
    result = await session.execute(stmt)
    return (getattr(result, "rowcount", 0) or 0) == 1


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
    """Fetch closed candles in `[since, until]` oldest-first, capped at `limit`.

    Defensive closed-candle filter: even though the live ingestor only persists
    candles where `kline.is_closed == True`, we additionally clamp the upper
    bound to `floor_to_timeframe(now, tf)` so that:
      - if test fixtures or future ingestors slip in a forming candle, the
        rules engine / backtest engine never sees it
      - tools and indicators that compute on this output can rely on every
        row representing a fully-closed bar
    """
    effective_until = floor_to_timeframe(datetime.now(tz=UTC), timeframe)
    if until is not None and until < effective_until:
        effective_until = until
    stmt = (
        select(OHLCV)
        .where(
            OHLCV.exchange == exchange,
            OHLCV.symbol == symbol,
            OHLCV.timeframe == timeframe,
            OHLCV.ts < effective_until,
        )
        .order_by(OHLCV.ts.desc())
        .limit(limit)
    )
    if since is not None:
        stmt = stmt.where(OHLCV.ts >= since)
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

    stmt = (
        select(func.count())
        .select_from(OHLCV)
        .where(
            OHLCV.exchange == exchange,
            OHLCV.symbol == symbol,
            OHLCV.timeframe == timeframe,
        )
    )
    return int((await session.execute(stmt)).scalar_one())


async def last_ts(
    session: AsyncSession,
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
) -> datetime | None:
    """Most recent persisted candle timestamp, or None if the series is empty."""
    from sqlalchemy import func

    stmt = (
        select(func.max(OHLCV.ts))
        .where(
            OHLCV.exchange == exchange,
            OHLCV.symbol == symbol,
            OHLCV.timeframe == timeframe,
        )
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def existing_ts_in_window(
    session: AsyncSession,
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
    since: datetime,
    until: datetime,
) -> set[datetime]:
    """Returns the set of `ts` already persisted in `[since, until)`.

    Used by the gap-fill flow to detect mid-history holes: `last_ts` only
    catches gaps at the tail of the series — if a single candle was missed
    in the middle and then live ingestion caught up, MAX(ts) jumps over the
    hole and that hole persists forever. With this set, the caller can
    diff against the expected `generate_series(since, until-delta, delta)`
    and fetch only the missing candles.
    """
    stmt = select(OHLCV.ts).where(
        OHLCV.exchange == exchange,
        OHLCV.symbol == symbol,
        OHLCV.timeframe == timeframe,
        OHLCV.ts >= since,
        OHLCV.ts < until,
    )
    result = await session.execute(stmt)
    return {row[0] for row in result.all()}
