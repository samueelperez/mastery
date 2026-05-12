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
from datetime import UTC, datetime, timedelta

import structlog

from app.agent.tools._time import floor_to_timeframe
from app.broadcasting.pubsub import market_channel, publish_json
from app.config import get_settings
from app.data.binance_adapter import EXCHANGE_NAME, BinanceAdapter
from app.data.exchange_context import ExchangeContext
from app.data.normalizer import timeframe_delta
from app.db import session_scope
from app.observability.metrics import gap_fill_inserts_total, runtime_streams_alive
from app.storage.ohlcv_repo import bulk_upsert, existing_ts_in_window, last_ts, upsert_one

log = structlog.get_logger(__name__)


# Timeframes que la ingesta live mantiene por cada símbolo de la watchlist.
# El agente usa los altos para multi-TF confluence + structure analysis.
TIMEFRAMES: tuple[str, ...] = ("1m", "15m", "1h", "4h", "1d")


def get_watch_list() -> list[tuple[str, str]]:
    """Producto cartesiano de Settings.watch_symbol_list × TIMEFRAMES.

    Cambios en WATCH_SYMBOLS requieren reiniciar la API: el set de streams
    CCXT pro y los loops del runtime de alertas se materializan al arranque.
    `get_settings` está cacheada (lru_cache) así que llamar esto múltiples
    veces dentro del mismo proceso es barato.
    """
    return [(sym, tf) for sym in get_settings().watch_symbol_list for tf in TIMEFRAMES]


# Velas iniciales cuando un símbolo arranca con la serie vacía. 1000 cubre:
#   1m → ~16h | 15m → ~10d | 1h → ~42d | 4h → ~167d | 1d → ~3y
# Suficiente para que el chart frontend (limit=500) tenga histórico decente sin
# tirarle a Binance los años que pediría el backfill CLI dedicado.
INITIAL_SEED_CANDLES = 1000


# Ventana retrospectiva que `_fill_gap` escanea para detectar huecos
# mid-history. `last_ts` solo cubre el agujero entre el último ts y now;
# si una vela intermedia se perdió y luego llegó la siguiente, MAX(ts)
# salta por encima y el hueco queda permanente sin esta ventana.
# 500 candles cubre lo que el frontend renderiza (`limit=500`) + margen.
LOOKBACK_GAP_SCAN_CANDLES = 500


async def _watch_loop(adapter: BinanceAdapter, symbol: str, timeframe: str) -> None:
    channel = market_channel(exchange=EXCHANGE_NAME, symbol=symbol, timeframe=timeframe)
    log.info("ingest.watch.start", symbol=symbol, timeframe=timeframe, channel=channel)
    last_persisted_ts: object = None

    while True:
        try:
            # Antes de (re)suscribirse al WS, rellenar cualquier hueco creado
            # por un disconnect previo. La primera iteración tras `start()`
            # es no-op (gap_fill ya corrió allí); las siguientes tras una
            # reconexión sí rellenan las velas cerradas durante el blackout.
            # bulk_upsert es idempotente, así que solapar con start() es safe.
            # `phase='reconnect'` distingue en métricas de la pasada de startup
            # (cualquier insert >0 mid-runtime indica WS inestable).
            with contextlib.suppress(Exception):
                await _fill_gap(
                    adapter, symbol, timeframe, phase="reconnect"
                )
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
                    # No log per persist — el evento es rutina (1m × 4 símbolos
                    # = ~6/min) y enterraba señales útiles. Errores y
                    # reconexiones siguen logueados (warning/info).
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


def _group_consecutive(timestamps: list[datetime], delta: timedelta) -> list[tuple[datetime, datetime]]:
    """Agrupa `timestamps` ordenados en rangos `[start, end]` donde cada par
    consecutivo está separado exactamente por `delta`. Devuelve tuplas (start, end).

    Para 5 missing ts contiguos devuelve un único range; para 3 missing seguidos
    + 1 missing aislado más adelante devuelve dos ranges. Esto permite hacer
    una sola `fetch_ohlcv_page` por cluster contiguo en vez de una por ts.
    """
    if not timestamps:
        return []
    ranges: list[tuple[datetime, datetime]] = []
    start = prev = timestamps[0]
    for ts in timestamps[1:]:
        if ts == prev + delta:
            prev = ts
        else:
            ranges.append((start, prev))
            start = prev = ts
    ranges.append((start, prev))
    return ranges


async def _fill_gap(
    adapter: BinanceAdapter,
    symbol: str,
    timeframe: str,
    *,
    phase: str = "startup",
) -> int:
    """Page-fetch any candles missing in the recent window.

    Tres escenarios:
      - **Serie vacía** (símbolo recién añadido a WATCH_SYMBOLS): seed inicial
        de `INITIAL_SEED_CANDLES` velas. Sin esto, hasta que cierre la primera
        vela post-arranque, el chart frontend (limit=500) recibe lista vacía.
      - **Tail gap** (API estuvo apagada / WS estaba desconectada al boot):
        hueco entre `last_ts` y `floor_now`. Page-fetch desde `last_ts + delta`.
      - **Mid-history gaps**: una vela perdida entre dos persistidas (WS drop
        breve mid-runtime que `_watch_loop` no rellenó en su día). `last_ts`
        salta sobre el hueco y nunca se ve. Escaneamos los últimos
        `LOOKBACK_GAP_SCAN_CANDLES` ts esperados, diffeamos contra los
        existentes en BD y fetcheamos solo los cluster contiguos faltantes.

    Returns the number of newly inserted candles.
    """
    delta = timeframe_delta(timeframe)
    floor_now = floor_to_timeframe(datetime.now(tz=UTC), timeframe)

    async with session_scope() as session:
        latest = await last_ts(
            session, exchange=EXCHANGE_NAME, symbol=symbol, timeframe=timeframe
        )

    # Empty series — seed flow.
    if latest is None:
        gap_start = floor_now - INITIAL_SEED_CANDLES * delta
        log.info(
            "ingest.gap_fill.seed_empty_series",
            symbol=symbol,
            timeframe=timeframe,
            from_=gap_start.isoformat(),
            to=floor_now.isoformat(),
            candles=INITIAL_SEED_CANDLES,
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
                break
            cursor_ms = new_cursor
        log.info(
            "ingest.gap_fill.done",
            symbol=symbol,
            timeframe=timeframe,
            inserted=inserted,
        )
        if inserted > 0:
            gap_fill_inserts_total.labels(
                symbol=symbol, timeframe=timeframe, phase=phase
            ).inc(inserted)
        return inserted

    # Non-empty series — find ALL missing ts in the lookback window.
    window_start = floor_now - LOOKBACK_GAP_SCAN_CANDLES * delta
    async with session_scope() as session:
        existing = await existing_ts_in_window(
            session,
            exchange=EXCHANGE_NAME,
            symbol=symbol,
            timeframe=timeframe,
            since=window_start,
            until=floor_now,
        )
    # Expected timestamps: aligned to the timeframe grid via floor_to_timeframe(now).
    # generate_series local en Python — `until=floor_now` se excluye porque la vela
    # de "ahora" todavía no ha cerrado (los candles se persisten al cierre).
    expected: list[datetime] = []
    ts_cursor = window_start
    while ts_cursor < floor_now:
        expected.append(ts_cursor)
        ts_cursor += delta
    missing = sorted(ts for ts in expected if ts not in existing)
    if not missing:
        return 0

    ranges = _group_consecutive(missing, delta)
    log.info(
        "ingest.gap_fill.start",
        symbol=symbol,
        timeframe=timeframe,
        n_missing=len(missing),
        n_ranges=len(ranges),
        oldest=missing[0].isoformat(),
        newest=missing[-1].isoformat(),
    )
    inserted = 0
    for range_start, range_end in ranges:
        # Pedimos `limit` suficiente para cubrir el rango completo más margen.
        # Binance permite hasta 1000 por call; rangos más largos se trocean.
        range_candles = int((range_end - range_start) / delta) + 1
        cursor_ms = int(range_start.timestamp() * 1000)
        end_ms = int(range_end.timestamp() * 1000) + int(delta.total_seconds() * 1000)
        remaining = range_candles
        while cursor_ms < end_ms and remaining > 0:
            page_limit = min(1000, remaining)
            candles = await adapter.fetch_ohlcv_page(
                symbol, timeframe, since_ms=cursor_ms, limit=page_limit
            )
            if not candles:
                break
            async with session_scope() as session:
                inserted += await bulk_upsert(session, candles)
            last_candle_ts = candles[-1].ts
            new_cursor = int(last_candle_ts.timestamp() * 1000) + int(delta.total_seconds() * 1000)
            if new_cursor <= cursor_ms:
                break
            cursor_ms = new_cursor
            remaining -= len(candles)

    log.info(
        "ingest.gap_fill.done",
        symbol=symbol,
        timeframe=timeframe,
        inserted=inserted,
    )
    if inserted > 0:
        gap_fill_inserts_total.labels(
            symbol=symbol, timeframe=timeframe, phase=phase
        ).inc(inserted)
    return inserted


class LiveIngestion:
    """Lifecycle owner. Call `start()` from FastAPI lifespan startup, and
    `stop()` on shutdown.
    """

    def __init__(self) -> None:
        self._adapter: BinanceAdapter | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._watch_list: list[tuple[str, str]] = get_watch_list()

    async def start(self) -> None:
        if self._adapter is not None:
            return
        self._adapter = BinanceAdapter(ExchangeContext.MAINNET_RO)
        symbols = sorted({s for s, _ in self._watch_list})
        log.info(
            "ingest.watchlist",
            symbols=symbols,
            timeframes=list(TIMEFRAMES),
            n_streams=len(self._watch_list),
        )
        # Close any gap from the previous shutdown BEFORE the WS subscriptions
        # take over — the WS only delivers NEW closes, never replays the past.
        # Run pairs in parallel; bulk_upsert is idempotent so any race with
        # the WS spawning below is safe (ON CONFLICT DO NOTHING).
        results = await asyncio.gather(
            *(_fill_gap(self._adapter, symbol, tf) for symbol, tf in self._watch_list),
            return_exceptions=True,
        )
        total_filled = sum(r for r in results if isinstance(r, int))
        log.info("ingest.gap_fill.total", inserted=total_filled)

        for symbol, tf in self._watch_list:
            t = asyncio.create_task(
                _watch_loop(self._adapter, symbol, tf),
                name=f"ingest:{symbol}:{tf}",
            )
            self._tasks.append(t)
        runtime_streams_alive.set(len(self._tasks))
        log.info("ingest.start", n_streams=len(self._tasks))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self._tasks.clear()
        runtime_streams_alive.set(0)
        if self._adapter is not None:
            await self._adapter.close()
            self._adapter = None
        log.info("ingest.stop")
