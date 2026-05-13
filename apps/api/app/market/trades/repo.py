"""Persistence for raw aggressor-tagged trades.

Used by `LiveIngestion._watch_trades_loop` to flush WS-captured trades to
`market_trades`. No SQL-level dedup: ccxt.pro.watchTrades pushes new
trades only, and downstream Provider A is robust to small reconnect
duplicates (price-bucket aggregation absorbs them).
"""

from collections.abc import Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exchanges.types import Trade


async def bulk_insert(session: AsyncSession, trades: Iterable[Trade]) -> int:
    """Insert a batch of trades. Returns rows actually written."""
    rows = [
        {
            "ts": t.ts,
            "exchange": t.exchange,
            "symbol": t.symbol,
            "price": t.price,
            "size": t.size,
            "side": t.side,
            "trade_id": t.trade_id,
        }
        for t in trades
    ]
    if not rows:
        return 0

    stmt = text(
        """
        INSERT INTO market_trades
            (ts, exchange, symbol, price, size, side, trade_id)
        VALUES
            (:ts, :exchange, :symbol, :price, :size, :side, :trade_id)
        """
    )
    result = await session.execute(stmt, rows)
    return getattr(result, "rowcount", 0) or 0
