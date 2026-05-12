"""Thin CCXT spot adapter — read-only, no ingestion.

Used by basis-related tools that need current spot price + recent spot
OHLCV history for a USDT pair. Kept deliberately minimal: no caching, no
WebSocket, no batched backfill. Callers (typically agent tools) wrap the
fetches with Redis caching at their layer.

Spot pairs use CCXT format ``"BTC/USDT"``. Symbols passed in as
``"BTCUSDT"`` (the perp convention) are auto-formatted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import ccxt.pro as ccxtpro

from app.data.normalizer import normalize_ccxt_ohlcv
from app.data.types import OHLCVCandle

if TYPE_CHECKING:
    from ccxt.async_support.base.exchange import Exchange


SPOT_EXCHANGE_NAME = "binance_spot"


def _to_spot_symbol(symbol: str) -> str:
    """Map a perp-style symbol (``"BTCUSDT"``) to CCXT spot format
    (``"BTC/USDT"``). Already-formatted spot symbols pass through."""
    s = symbol.upper().strip()
    if "/" in s:
        return s
    for quote in ("USDT", "BUSD", "USDC", "BTC", "ETH"):
        if s.endswith(quote):
            base = s[: -len(quote)]
            if base:
                return f"{base}/{quote}"
    return s  # let CCXT raise if unsupported


class BinanceSpotAdapter:
    """Minimal spot wrapper. Always read-only — no API key needed."""

    def __init__(self) -> None:
        self.client: Exchange = ccxtpro.binance(
            {
                "enableRateLimit": True,
                "options": {"defaultType": "spot"},
            }
        )

    async def close(self) -> None:
        await self.client.close()

    async def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        ticker: dict[str, Any] = await self.client.fetch_ticker(
            _to_spot_symbol(symbol)
        )
        return ticker

    async def fetch_ohlcv_page(
        self,
        symbol: str,
        timeframe: str,
        *,
        since_ms: int | None = None,
        limit: int = 1000,
    ) -> list[OHLCVCandle]:
        rows = await self.client.fetch_ohlcv(
            _to_spot_symbol(symbol), timeframe, since=since_ms, limit=limit
        )
        return normalize_ccxt_ohlcv(
            rows, exchange=SPOT_EXCHANGE_NAME, symbol=symbol, timeframe=timeframe
        )
