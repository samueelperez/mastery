from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from app.core.exchanges.types import OHLCVCandle, Trade

# Map CCXT timeframe strings to a (count, unit) pair so we can compute
# the kline's expected close time (used to infer is_closed).
_TIMEFRAME_DELTAS: dict[str, timedelta] = {
    "1m": timedelta(minutes=1),
    "3m": timedelta(minutes=3),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "2h": timedelta(hours=2),
    "4h": timedelta(hours=4),
    "6h": timedelta(hours=6),
    "8h": timedelta(hours=8),
    "12h": timedelta(hours=12),
    "1d": timedelta(days=1),
    "3d": timedelta(days=3),
    "1w": timedelta(weeks=1),
}


def timeframe_delta(timeframe: str) -> timedelta:
    try:
        return _TIMEFRAME_DELTAS[timeframe]
    except KeyError as exc:
        raise ValueError(f"Unsupported timeframe: {timeframe}") from exc


def normalize_ccxt_trade(
    row: dict,
    *,
    exchange: str,
) -> Trade:
    """Convert a single ccxt trade dict to our Trade model.

    ccxt trade fields used:
        - timestamp: ms since epoch
        - symbol: e.g. 'BTC/USDT:USDT' for USDM perp; we keep the symbol
          the caller passed to watch_trades (no slash) by overriding.
        - price, amount: floats
        - side: 'buy' or 'sell' (aggressor / taker side)
        - id: trade id (optional; some exchanges omit)

    Caller must pass `symbol` if it wants the internal form (e.g.
    'BTCUSDT') rather than ccxt's slashed form.
    """
    ts_ms = row["timestamp"]
    raw_side = row["side"]
    if raw_side == "buy":
        side = "B"
    elif raw_side == "sell":
        side = "S"
    else:
        raise ValueError(f"Unexpected trade side from ccxt: {raw_side!r}")
    return Trade(
        exchange=exchange,
        symbol=row["symbol"],
        ts=datetime.fromtimestamp(ts_ms / 1000, tz=UTC),
        price=float(row["price"]),
        size=float(row["amount"]),
        side=side,
        trade_id=str(row["id"]) if row.get("id") is not None else None,
    )


def normalize_ccxt_ohlcv(
    rows: Iterable[list[float]],
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
    now: datetime | None = None,
) -> list[OHLCVCandle]:
    """Convert CCXT's positional [ts_ms, o, h, l, c, v] tuples to OHLCVCandle.

    A candle is considered closed when its expected end time is in the past.
    We accept an explicit `now` for testability; otherwise use UTC clock.
    """
    if now is None:
        now = datetime.now(tz=UTC)
    delta = timeframe_delta(timeframe)
    out: list[OHLCVCandle] = []
    for row in rows:
        ts_ms, o, h, l, c, v = row
        ts = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
        candle_end = ts + delta
        out.append(
            OHLCVCandle(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                ts=ts,
                o=float(o),
                h=float(h),
                l=float(l),
                c=float(c),
                v=float(v),
                is_closed=candle_end <= now,
            )
        )
    return out
