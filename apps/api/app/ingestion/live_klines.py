"""Long-lived task: watch Binance kline WS streams, fan out via Valkey pub/sub,
persist closed candles to Postgres.

Started by FastAPI's lifespan. One asyncio task per (symbol, timeframe) pair.
For F0 we hardcode BTCUSDT @ 1m and 1h; F1 will accept a config-driven set.
"""

from __future__ import annotations

import asyncio
import contextlib

import structlog

from app.broadcasting.pubsub import market_channel, publish_json
from app.data.binance_adapter import EXCHANGE_NAME, BinanceAdapter
from app.data.exchange_context import ExchangeContext
from app.db import session_scope
from app.storage.ohlcv_repo import upsert_one

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
