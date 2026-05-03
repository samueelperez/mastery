"""Timeframe helpers shared by tools."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

_TF_DELTAS: dict[str, timedelta] = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}


def floor_to_timeframe(ts: datetime, timeframe: str) -> datetime:
    """Return the start of the candle that contains `ts`, in UTC.

    Used to filter out forming candles: any candle whose start ≥ floor(now, tf)
    is the current open candle (not yet closed) and must be excluded.
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    ts = ts.astimezone(UTC)
    delta = _TF_DELTAS[timeframe]
    if timeframe == "1d":
        return ts.replace(hour=0, minute=0, second=0, microsecond=0)
    if timeframe.endswith("h"):
        h = (ts.hour // (delta.seconds // 3600)) * (delta.seconds // 3600)
        return ts.replace(hour=h, minute=0, second=0, microsecond=0)
    minutes = delta.seconds // 60
    m = (ts.minute // minutes) * minutes
    return ts.replace(minute=m, second=0, microsecond=0)


def staleness_warning(*, last_closed: datetime, timeframe: str, now: datetime | None = None) -> str | None:
    """If the most recent closed candle is more than 2x the timeframe behind now,
    return a human-readable warning string. Otherwise None.
    """
    if now is None:
        now = datetime.now(tz=UTC)
    delta = _TF_DELTAS[timeframe]
    age = now - last_closed
    if age > 2 * delta:
        return f"stale: last closed candle {age} ago, timeframe is {timeframe}"
    return None
