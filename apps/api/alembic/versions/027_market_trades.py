"""Trades capture for liquidation Provider A.

Revision ID: 027
Revises: 026
Create Date: 2026-05-12

Adds `market_trades` table to persist aggressor-tagged trades captured by
LiveIngestion via `ccxt.pro.watchTrades`. Consumed by
`app.liquidation.providers.derived.DerivedLiquidationProvider` (Cerebro 1,
Day 2) to estimate counterparty liquidation prices.

Schema choice notes:
- `side TEXT CHECK side IN ('B','S')` follows Binance's raw aggressor flag.
  ccxt 'buy'/'sell' is normalized to 'B'/'S' at ingestion time.
- PK is `(id, ts)` so the hypertable's partitioning column (`ts`) is part of
  the unique constraint. We do NOT enforce trade-id dedup — ccxt.pro.watchTrades
  pushes new trades only, and rare duplicates from WS reconnects produce
  negligible noise (downstream Provider A aggregates by price-bucket sums).
- Hypertable is created only when Timescale extension is loaded — same
  pattern as `001_init_ohlcv_hypertable.py` so Railway (no Timescale) keeps
  working.
"""
from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "027"
down_revision: str | None = "026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE market_trades (
            id         BIGSERIAL    NOT NULL,
            ts         TIMESTAMPTZ  NOT NULL,
            exchange   TEXT         NOT NULL,
            symbol     TEXT         NOT NULL,
            price      NUMERIC(20, 8) NOT NULL,
            size       NUMERIC(20, 8) NOT NULL,
            side       TEXT         NOT NULL,
            trade_id   TEXT,
            PRIMARY KEY (id, ts),
            CONSTRAINT market_trades_side_check CHECK (side IN ('B', 'S')),
            CONSTRAINT market_trades_price_positive CHECK (price > 0),
            CONSTRAINT market_trades_size_positive CHECK (size > 0)
        )
        """
    )

    op.execute(
        "CREATE INDEX market_trades_symbol_ts "
        "ON market_trades (symbol, ts DESC)"
    )
    op.execute("CREATE INDEX market_trades_ts ON market_trades (ts DESC)")

    bind = op.get_bind()
    timescale_loaded = bind.execute(
        text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb' LIMIT 1")
    ).first() is not None
    if timescale_loaded:
        op.execute(
            "SELECT create_hypertable("
            "'market_trades', 'ts', "
            "chunk_time_interval => interval '1 day', "
            "if_not_exists => TRUE"
            ")"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS market_trades")
