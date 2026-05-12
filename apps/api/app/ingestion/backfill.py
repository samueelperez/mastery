"""Historical OHLCV backfill from Binance USDT-M Futures (mainnet, public).

Usage:
    uv run python -m app.ingestion.backfill --symbol BTCUSDT --tf 1m --years 2

Strategy:
    - Page through CCXT `fetch_ohlcv` in 1500-row chunks (Binance USDT-M cap).
    - 50ms sleep between requests + jitter to stay under rate limits.
    - Idempotent: ON CONFLICT DO NOTHING via the storage repo.
    - Logs progress every N pages so the user can see it works.
"""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime, timedelta

import structlog
import typer

from app.core.db import dispose_engine, init_engine, session_scope
from app.core.exchanges.binance_adapter import EXCHANGE_NAME, BinanceAdapter
from app.core.exchanges.exchange_context import ExchangeContext
from app.core.exchanges.normalizer import timeframe_delta
from app.storage.ohlcv_repo import bulk_upsert, count_rows

log = structlog.get_logger(__name__)

PAGE_LIMIT = 1000  # Binance USDT-M Futures effective cap per fetch_ohlcv via CCXT.
INTER_REQUEST_SLEEP = 0.05  # seconds; ~20 req/s well under any limit.

app = typer.Typer(add_completion=False, no_args_is_help=True)


async def _run(symbol: str, timeframe: str, years: float) -> None:
    init_engine()
    adapter = BinanceAdapter(ExchangeContext.MAINNET_RO)

    delta = timeframe_delta(timeframe)
    end = datetime.now(tz=UTC)
    # Use seconds so fractional years work for quick smoke tests (e.g. years=0.001 -> ~8h).
    start = end - timedelta(seconds=years * 365.25 * 86400)
    since_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    log.info(
        "backfill.start",
        exchange=EXCHANGE_NAME,
        symbol=symbol,
        timeframe=timeframe,
        years=years,
        start=start.isoformat(),
        end=end.isoformat(),
    )

    total_inserted = 0
    page = 0
    cursor_ms = since_ms

    try:
        while cursor_ms < end_ms:
            candles = await adapter.fetch_ohlcv_page(
                symbol, timeframe, since_ms=cursor_ms, limit=PAGE_LIMIT
            )
            if not candles:
                log.info("backfill.empty_page", cursor_ms=cursor_ms)
                break

            async with session_scope() as session:
                inserted = await bulk_upsert(session, candles)

            total_inserted += inserted
            page += 1
            last_ts = candles[-1].ts
            new_cursor = int(last_ts.timestamp() * 1000) + int(delta.total_seconds() * 1000)

            if page % 20 == 0:
                log.info(
                    "backfill.progress",
                    page=page,
                    last_ts=last_ts.isoformat(),
                    inserted_total=total_inserted,
                    cursor_pct=round((cursor_ms - since_ms) / max(end_ms - since_ms, 1) * 100, 1),
                )

            # Safety: ensure we make progress; otherwise CCXT is returning the same page.
            if new_cursor <= cursor_ms:
                log.warning("backfill.no_progress", cursor_ms=cursor_ms)
                break
            cursor_ms = new_cursor

            await asyncio.sleep(INTER_REQUEST_SLEEP + random.random() * 0.02)

        async with session_scope() as session:
            final_count = await count_rows(
                session, exchange=EXCHANGE_NAME, symbol=symbol, timeframe=timeframe
            )

        log.info(
            "backfill.done",
            symbol=symbol,
            timeframe=timeframe,
            pages=page,
            inserted=total_inserted,
            total_rows=final_count,
        )
    finally:
        await adapter.close()
        await dispose_engine()


@app.command()
def main(
    symbol: str = typer.Option("BTCUSDT", "--symbol", "-s", help="Symbol, e.g. BTCUSDT"),
    timeframe: str = typer.Option("1m", "--tf", "-t", help="Timeframe, e.g. 1m"),
    years: float = typer.Option(2.0, "--years", "-y", help="Years of history to backfill"),
) -> None:
    asyncio.run(_run(symbol, timeframe, years))


if __name__ == "__main__":
    app()
