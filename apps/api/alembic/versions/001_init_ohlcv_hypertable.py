"""init: extensions + ohlcv hypertable + columnar compression policy

Revision ID: 001
Revises:
Create Date: 2026-05-03
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. pgvector es OBLIGATORIO (F2 embeddings).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # TimescaleDB es OPCIONAL: si el provider no lo tiene (Railway, Neon
    # free, RDS sin extension) saltamos las funciones específicas. Sin
    # Timescale el ohlcv queda como tabla regular — funciona igual, solo
    # más lento y sin compresión columnar a partir de ~5M filas.
    # Local dev con docker-compose sí tiene Timescale; prod en Railway no.
    #
    # IMPORTANTE: chequeamos pg_available_extensions ANTES de CREATE
    # porque un CREATE EXTENSION fallido contamina la transacción de
    # Alembic ("current transaction is aborted, commands ignored").
    bind = op.get_bind()
    timescale_ok = bind.execute(
        text(
            "SELECT 1 FROM pg_available_extensions "
            "WHERE name = 'timescaledb' LIMIT 1"
        )
    ).first() is not None
    if timescale_ok:
        op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

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

    # 4. Index on (symbol, timeframe, ts DESC) — primary query shape:
    #    "give me last N candles for BTCUSDT 1h". SIEMPRE útil.
    op.execute(
        """
        CREATE INDEX ohlcv_symbol_tf_ts_desc
            ON ohlcv (symbol, timeframe, ts DESC)
        """
    )

    # 3-5. Funciones específicas de Timescale solo si está disponible.
    if timescale_ok:
        op.execute(
            "SELECT create_hypertable('ohlcv', 'ts', chunk_time_interval => INTERVAL '7 days')"
        )
        op.execute(
            """
            ALTER TABLE ohlcv SET (
                timescaledb.compress = true,
                timescaledb.compress_segmentby = 'exchange,symbol,timeframe',
                timescaledb.compress_orderby   = 'ts DESC'
            )
            """
        )
        op.execute("SELECT add_compression_policy('ohlcv', INTERVAL '7 days')")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ohlcv CASCADE")
    # Leave extensions in place — other migrations may depend on them.
