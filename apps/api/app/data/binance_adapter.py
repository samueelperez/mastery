"""Binance USDT-M Futures adapter.

CCXT 4.4+ ships CCXT Pro (WebSocket) bundled into the OSS package — no separate
license. We use `ccxt.pro.binanceusdm` for both REST (paginated `fetch_ohlcv`)
and WebSocket (`watch_ohlcv`).

Dual-context from day one (see `ExchangeContext`):
- MAINNET_RO  — public market data; no API key needed.  ← F0 only uses this.
- TESTNET     — Binance futures testnet for execution + simulated OI/funding.
- MAINNET_LIVE— real money. Not used until F6+.

`set_sandbox_mode(True)` is CCXT's switch to testnet endpoints.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import ccxt.pro as ccxtpro
import structlog

from app.data.exchange_context import ExchangeContext
from app.data.normalizer import normalize_ccxt_ohlcv
from app.data.types import OHLCVCandle

if TYPE_CHECKING:
    from ccxt.async_support.base.exchange import Exchange  # noqa: TC003


log = structlog.get_logger(__name__)

EXCHANGE_NAME = "binance_usdm"


class BinanceAdapter:
    """Thin async wrapper around ccxt.pro.binanceusdm.

    Owns a single CCXT client; callers must `await close()` to release WS / HTTP
    connections (typically wired to FastAPI's lifespan).
    """

    def __init__(
        self,
        ctx: ExchangeContext,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
    ) -> None:
        if ctx.needs_api_key and (api_key is None or api_secret is None):
            raise ValueError(f"Context {ctx.value} requires api_key + api_secret")

        self.ctx = ctx
        # ccxt typing is loose; we accept the dict params as-is.
        self.client: Exchange = ccxtpro.binanceusdm(
            {
                "enableRateLimit": True,
                "apiKey": api_key or "",
                "secret": api_secret or "",
                "options": {"defaultType": "future"},
            }
        )
        if ctx is ExchangeContext.TESTNET:
            self.client.set_sandbox_mode(True)

    async def close(self) -> None:
        await self.client.close()

    # -------------------------------------------------------------------------
    # REST: historical OHLCV (used by backfill).
    # -------------------------------------------------------------------------

    async def fetch_ohlcv_page(
        self,
        symbol: str,
        timeframe: str,
        *,
        since_ms: int | None = None,
        limit: int = 1500,
    ) -> list[OHLCVCandle]:
        """One paginated REST call. Binance USDT-M caps at 1500 klines/req."""
        rows = await self.client.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=limit)
        return normalize_ccxt_ohlcv(
            rows, exchange=EXCHANGE_NAME, symbol=symbol, timeframe=timeframe
        )

    # -------------------------------------------------------------------------
    # WS: live OHLCV (used by ingestion).
    # -------------------------------------------------------------------------

    async def watch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
    ) -> AsyncIterator[OHLCVCandle]:
        """Async generator yielding the latest candle on every WS update.

        Yields the *current* (possibly-forming) candle; consumers must check
        `candle.is_closed` before treating it as final. CCXT doesn't expose
        Binance's `x` flag through this abstraction, so we infer is_closed
        from the kline's expected end vs. wallclock (see normalizer).
        """
        while True:
            rows = await self.client.watch_ohlcv(symbol, timeframe)
            if not rows:
                continue
            normalized = normalize_ccxt_ohlcv(
                rows, exchange=EXCHANGE_NAME, symbol=symbol, timeframe=timeframe
            )
            # CCXT returns a list of recent klines; the last one is the live one.
            yield normalized[-1]
