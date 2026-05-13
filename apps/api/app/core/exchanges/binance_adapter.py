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

from app.core.exchanges.exchange_context import ExchangeContext
from app.core.exchanges.normalizer import normalize_ccxt_ohlcv, normalize_ccxt_trade
from app.core.exchanges.types import OHLCVCandle, Trade

if TYPE_CHECKING:
    from ccxt.async_support.base.exchange import Exchange


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
        if ctx.needs_api_key and not (api_key and api_secret):
            raise ValueError(f"Context {ctx.value} requires non-empty api_key + api_secret")

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

    # -------------------------------------------------------------------------
    # WS: live trades (used by liquidation Provider A — Cerebro 1).
    # -------------------------------------------------------------------------

    async def watch_trades(
        self,
        symbol: str,
    ) -> AsyncIterator[Trade]:
        """Async generator yielding every aggressor-tagged trade on WS push.

        Each ccxt.pro `watch_trades` poll returns a *batch* of trades since
        the previous push; we flatten and yield one Trade at a time so the
        ingestion loop can buffer at its own cadence.

        `symbol` follows the watch_list convention (e.g. 'BTCUSDT'). We pass
        it straight to ccxt — ccxt.pro.binanceusdm accepts this form.
        """
        while True:
            rows = await self.client.watch_trades(symbol)
            if not rows:
                continue
            for row in rows:
                # ccxt returns 'symbol' as 'BTC/USDT:USDT' in some cases;
                # override with the caller's internal form so DB rows are
                # consistent regardless of ccxt's normalization choices.
                row_for_normalize = {**row, "symbol": symbol}
                yield normalize_ccxt_trade(row_for_normalize, exchange=EXCHANGE_NAME)

    # -------------------------------------------------------------------------
    # REST: funding rate (perpetuals).
    # -------------------------------------------------------------------------

    async def fetch_funding_rate(self, symbol: str) -> dict:
        """Current funding rate for a perpetual contract.

        Returns CCXT-normalized dict with keys: `fundingRate` (decimal, e.g.
        0.0001 = 0.01% per 8h), `fundingTimestamp`, `nextFundingTimestamp`.
        Binance USDT-M perps cobran funding cada 8h (00:00, 08:00, 16:00 UTC).
        """
        return await self.client.fetch_funding_rate(symbol)

    async def fetch_funding_rate_history(
        self,
        symbol: str,
        *,
        since_ms: int | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Historial de funding rates (último N pagos cada 8h).

        Útil para calcular funding cumulativo: 7 días = 21 pagos. Si la suma
        es positiva, los longs pagan a los shorts (alcista crowded).
        """
        return await self.client.fetch_funding_rate_history(symbol, since=since_ms, limit=limit)

    # -------------------------------------------------------------------------
    # REST: open interest (perpetuals).
    # -------------------------------------------------------------------------

    async def fetch_open_interest(self, symbol: str) -> dict:
        """Open interest actual del símbolo perpetuo.

        Returns CCXT-normalized dict con keys: `openInterestAmount` (en base
        currency, e.g. BTC), `openInterestValue` (en USDT), `timestamp`.
        """
        return await self.client.fetch_open_interest(symbol)

    async def fetch_open_interest_history(
        self,
        symbol: str,
        timeframe: str = "1h",
        *,
        limit: int = 100,
    ) -> list[dict]:
        """Historial de OI por timeframe (5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d).

        Útil para calcular delta y tendencia: subida de OI con precio = entrada
        de dinero nuevo (confirma trend); subida de OI con precio plano =
        squeeze building.
        """
        return await self.client.fetch_open_interest_history(
            symbol, timeframe=timeframe, limit=limit
        )

    # -------------------------------------------------------------------------
    # REST: ticker (last/mark/index price).
    # -------------------------------------------------------------------------

    async def fetch_ticker(self, symbol: str) -> dict:
        """Ticker snapshot. Útil para derivar OI en USDT cuando CCXT no
        expone `openInterestValue` directamente (caso Binance USDM en el
        endpoint `/fapi/v1/openInterest`). Multiplicar OI base × `last`.
        """
        return await self.client.fetch_ticker(symbol)
