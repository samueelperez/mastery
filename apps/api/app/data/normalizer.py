from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from app.data.types import OHLCVCandle

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
