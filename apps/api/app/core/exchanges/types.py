from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

TradeSide = Literal["B", "S"]


class Trade(BaseModel):
    """A single aggressor-tagged trade, normalized across exchanges.

    `side` follows Binance's raw aggressor flag: 'B' if the buyer was the
    taker (lifted the offer), 'S' if the seller was the taker (hit the bid).
    ccxt's 'buy'/'sell' is normalized at ingestion time.
    """

    model_config = ConfigDict(frozen=True)

    exchange: str
    symbol: str
    ts: datetime
    price: float
    size: float
    side: TradeSide
    trade_id: str | None = None


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
