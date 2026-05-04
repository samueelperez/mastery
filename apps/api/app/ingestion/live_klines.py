"""Long-lived task: watch Binance kline WS streams, fan out via Valkey pub/sub,
persist closed candles to Postgres.

Started by FastAPI's lifespan. One asyncio task per (symbol, timeframe) pair.
On startup, every pair runs `_fill_gap` first — if the API was offline for a
stretch (dev restart, deploy), the WS only catches NEW closes; the gap between
the last persisted candle and `floor_to_timeframe(now, tf)` would otherwise
appear as a hole forever (live ingest never looks backwards).
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime

import structlog

from app.agent.tools._time import floor_to_timeframe
from app.broadcasting.pubsub import market_channel, publish_json
from app.data.binance_adapter import EXCHANGE_NAME, BinanceAdapter
from app.data.exchange_context import ExchangeContext
from app.data.normalizer import timeframe_delta
from app.db import session_scope
from app.storage.ohlcv_repo import bulk_upsert, last_ts, upsert_one

log = structlog.get_logger(__name__)


# (symbol, timeframe) pairs to ingest. F1 expands the higher TFs the agent uses
# for multi-TF confluence + structure analysis.
WATCH_LIST: list[tuple[str, str]] = [
    ("BTCUSDT", "1m"),
    ("BTCUSDT", "15m"),
    ("BTCUSDT", "1h"),
    ("BTCUSDT", "4h"),
    ("BTCUSDT", "1d"),
]


async def _watch_loop(adapter: BinanceAdapter, symbol: str, timeframe: str) -> None:
    channel = market_channel(exchange=EXCHANGE_NAME, symbol=symbol, timeframe=timeframe)
    log.info("ingest.watch.start", symbol=symbol, timeframe=timeframe, channel=channel)
    last_persisted_ts: object = None

    while True:
        try:
            async for candle in adapter.watch_ohlcv(symbol, timeframe):
                # Always publish: subscribers get every tick of the forming candle.
                await publish_json(
                    channel,
                    {
                        "exchange": candle.exchange,
                        "symbol": candle.symbol,
                        "timeframe": candle.timeframe,
                        "ts": candle.ts.isoformat(),
                        "o": candle.o,
                        "h": candle.h,
                        "l": candle.l,
                        "c": candle.c,
                        "v": candle.v,
                        "is_closed": candle.is_closed,
                    },
                )

                # Persist only when the candle closes (and only once per ts).
                if candle.is_closed and candle.ts != last_persisted_ts:
                    async with session_scope() as session:
                        await upsert_one(session, candle)
                    last_persisted_ts = candle.ts
                    log.debug(
                        "ingest.persist",
                        symbol=symbol,
                        timeframe=timeframe,
                        ts=candle.ts.isoformat(),
                    )
        except asyncio.CancelledError:
            log.info("ingest.watch.cancelled", symbol=symbol, timeframe=timeframe)
            raise
        except Exception as exc:
            log.warning(
                "ingest.watch.error",
                symbol=symbol,
                timeframe=timeframe,
                error=str(exc),
            )
            await asyncio.sleep(2.0)  # backoff before reconnect


async def _fill_gap(
    adapter: BinanceAdapter, symbol: str, timeframe: str
) -> int:
    """Page-fetch any candles missing between the last persisted one and now.

    Runs once per (symbol, tf) at lifespan startup. If the series is empty
    (fresh DB) we leave it alone — the user should run the dedicated
    `app.ingestion.backfill` CLI for the initial 2y backfill instead of
    trying to do it here at boot.

    Returns the number of newly inserted candles.
    """
    delta = timeframe_delta(timeframe)
    floor_now = floor_to_timeframe(datetime.now(tz=UTC), timeframe)

    async with session_scope() as session:
        latest = await last_ts(
            session, exchange=EXCHANGE_NAME, symbol=symbol, timeframe=timeframe
        )
    if latest is None:
        log.info(
            "ingest.gap_fill.skip_empty_series", symbol=symbol, timeframe=timeframe
        )
        return 0
    # Next missing candle starts one delta after the last persisted one.
    gap_start = latest + delta
    if gap_start >= floor_now:
        return 0  # no gap

    log.info(
        "ingest.gap_fill.start",
        symbol=symbol,
        timeframe=timeframe,
        from_=gap_start.isoformat(),
        to=floor_now.isoformat(),
    )
    cursor_ms = int(gap_start.timestamp() * 1000)
    end_ms = int(floor_now.timestamp() * 1000)
    inserted = 0
    while cursor_ms < end_ms:
        candles = await adapter.fetch_ohlcv_page(
            symbol, timeframe, since_ms=cursor_ms, limit=1000
        )
        if not candles:
            break
        async with session_scope() as session:
            inserted += await bulk_upsert(session, candles)
        last_candle_ts = candles[-1].ts
        new_cursor = int(last_candle_ts.timestamp() * 1000) + int(delta.total_seconds() * 1000)
        if new_cursor <= cursor_ms:
            break  # no progress; bail
        cursor_ms = new_cursor

    log.info(
        "ingest.gap_fill.done",
        symbol=symbol,
        timeframe=timeframe,
        inserted=inserted,
    )
    return inserted


class LiveIngestion:
    """Lifecycle owner. Call `start()` from FastAPI lifespan startup, and
    `stop()` on shutdown.
    """

    def __init__(self) -> None:
        self._adapter: BinanceAdapter | None = None
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        if self._adapter is not None:
            return
        self._adapter = BinanceAdapter(ExchangeContext.MAINNET_RO)
        # Close any gap from the previous shutdown BEFORE the WS subscriptions
        # take over — the WS only delivers NEW closes, never replays the past.
        # Run pairs in parallel; bulk_upsert is idempotent so any race with
        # the WS spawning below is safe (ON CONFLICT DO NOTHING).
        results = await asyncio.gather(
            *(_fill_gap(self._adapter, symbol, tf) for symbol, tf in WATCH_LIST),
            return_exceptions=True,
        )
        total_filled = sum(r for r in results if isinstance(r, int))
        log.info("ingest.gap_fill.total", inserted=total_filled)

        for symbol, tf in WATCH_LIST:
            t = asyncio.create_task(
                _watch_loop(self._adapter, symbol, tf),
                name=f"ingest:{symbol}:{tf}",
            )
            self._tasks.append(t)
        log.info("ingest.start", n_streams=len(self._tasks))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self._tasks.clear()
        if self._adapter is not None:
            await self._adapter.close()
            self._adapter = None
        log.info("ingest.stop")
