"""init: extensions + ohlcv hypertable + columnar compression policy

Revision ID: 001
Revises:
Create Date: 2026-05-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Required extensions (pgvector is here so F2 doesn't need a separate migration)
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 2. OHLCV table — multi-symbol, multi-timeframe, multi-exchange from day one.
    #    Composite PK lets us idempotently upsert via ON CONFLICT DO NOTHING.
    op.execute(
        """
        CREATE TABLE ohlcv (
            exchange   TEXT NOT NULL,
            symbol     TEXT NOT NULL,
            timeframe  TEXT NOT NULL,
            ts         TIMESTAMPTZ NOT NULL,
            o          DOUBLE PRECISION NOT NULL,
            h          DOUBLE PRECISION NOT NULL,
            l          DOUBLE PRECISION NOT NULL,
            c          DOUBLE PRECISION NOT NULL,
            v          DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (exchange, symbol, timeframe, ts)
        )
        """
    )

    # 3. Convert to hypertable. 7-day chunk fits ~10K BTCUSDT 1m rows comfortably.
    op.execute(
        "SELECT create_hypertable('ohlcv', 'ts', chunk_time_interval => INTERVAL '7 days')"
    )

    # 4. Index on (symbol, timeframe, ts DESC) — primary query shape:
    #    "give me last N candles for BTCUSDT 1h".
    op.execute(
        """
        CREATE INDEX ohlcv_symbol_tf_ts_desc
            ON ohlcv (symbol, timeframe, ts DESC)
        """
    )

    # 5. Enable columnar compression. Tier-1 ordering by ts so most queries hit
    #    a single compressed segment.
    op.execute(
        """
        ALTER TABLE ohlcv SET (
            timescaledb.compress = true,
            timescaledb.compress_segmentby = 'exchange,symbol,timeframe',
            timescaledb.compress_orderby   = 'ts DESC'
        )
        """
    )
    # Compress chunks older than 7 days (most queries hit recent data uncompressed).
    op.execute("SELECT add_compression_policy('ohlcv', INTERVAL '7 days')")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ohlcv CASCADE")
    # Leave extensions in place — other migrations may depend on them.
