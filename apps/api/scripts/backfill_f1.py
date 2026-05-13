"""Backfill the multi-TF candles F1's tools depend on.

Run once before testing the chat agent end-to-end. 1h is already done (F0);
this adds 15m, 4h, 1d for BTCUSDT (and any future symbol you append below).

Usage: cd apps/api && uv run python scripts/backfill_f1.py
"""

from __future__ import annotations

import asyncio

import structlog

from app.market.ohlcv.backfill import _run

log = structlog.get_logger(__name__)


PAIRS_TO_BACKFILL: list[tuple[str, str, float]] = [
    # (symbol, timeframe, years)
    ("BTCUSDT", "15m", 2.0),
    ("BTCUSDT", "4h", 2.0),
    ("BTCUSDT", "1d", 2.0),
]


async def main() -> None:
    for symbol, tf, years in PAIRS_TO_BACKFILL:
        log.info("backfill_f1.pair.start", symbol=symbol, timeframe=tf, years=years)
        await _run(symbol, tf, years)
        log.info("backfill_f1.pair.done", symbol=symbol, timeframe=tf)


if __name__ == "__main__":
    asyncio.run(main())
