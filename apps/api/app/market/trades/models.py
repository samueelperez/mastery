from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Numeric, PrimaryKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.market.ohlcv.models import Base


class MarketTrade(Base):
    __tablename__ = "market_trades"

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exchange: Mapped[str] = mapped_column(String, nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    price: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    size: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    side: Mapped[str] = mapped_column(String, nullable=False)
    trade_id: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (PrimaryKeyConstraint("id", "ts"),)
