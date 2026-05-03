from datetime import datetime

from pydantic import BaseModel, ConfigDict


class OHLCVCandle(BaseModel):
    """A single OHLCV candle, normalized across exchanges.

    `is_closed` is inferred from the kline's expected close time vs. now —
    the blueprint forbids triggering signals on non-closed klines unless
    the consumer explicitly opts in.
    """

    model_config = ConfigDict(frozen=True)

    exchange: str
    symbol: str
    timeframe: str
    ts: datetime
    o: float
    h: float
    l: float
    c: float
    v: float
    is_closed: bool
