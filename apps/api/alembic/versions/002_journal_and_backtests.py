"""F2: journal_trades + bias_events + backtest_runs + strategy_metrics.

Revision ID: 002
Revises: 001
Create Date: 2026-05-03

The `vector` extension is already enabled by migration 001 (F0). We only need
gen_random_uuid() which is built-in to PG13+. journal_trades.embedding is
vector(1024) for voyage-4-large at default dim.

`tsv` is a GENERATED column over `summary_text` for BM25-style ranking via
ts_rank; combined with the dense embedding column it backs hybrid search via
Reciprocal Rank Fusion (see app/storage/journal_repo.py).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ----------------------------- journal_trades ----------------------------
    op.execute(
        """
        CREATE TABLE journal_trades (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id            text NOT NULL DEFAULT 'me',
            trade_ts           timestamptz NOT NULL,
            symbol             text NOT NULL,
            timeframe          text NOT NULL,
            mode               text NOT NULL CHECK (mode IN ('paper','live','manual_log','csv_import')),
            side               text NOT NULL CHECK (side IN ('long','short')),
            entry_px           numeric NOT NULL,
            exit_px            numeric,
            size               numeric NOT NULL,
            r_multiple         numeric,
            setup_tag          text NOT NULL,
            regime             text NOT NULL,
            mistakes           text,
            news_24h           jsonb NOT NULL DEFAULT '{}'::jsonb,
            features           jsonb NOT NULL DEFAULT '{}'::jsonb,
            summary_text       text NOT NULL,
            summary_hash       text NOT NULL,
            embedding_version  int  NOT NULL DEFAULT 1,
            embedding          vector(1024),
            tsv                tsvector GENERATED ALWAYS AS
                                 (to_tsvector('english', summary_text)) STORED,
            created_at         timestamptz NOT NULL DEFAULT now(),
            updated_at         timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX journal_hnsw ON journal_trades "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )
    op.execute("CREATE INDEX journal_tsv ON journal_trades USING gin (tsv)")
    op.execute(
        "CREATE INDEX journal_lookup ON journal_trades (user_id, mode, regime, setup_tag)"
    )
    op.execute(
        "CREATE INDEX journal_user_ts ON journal_trades (user_id, trade_ts DESC)"
    )

    # ----------------------------- bias_events ------------------------------
    op.execute(
        """
        CREATE TABLE bias_events (
            id           bigserial PRIMARY KEY,
            user_id      text NOT NULL DEFAULT 'me',
            detected_at  timestamptz NOT NULL DEFAULT now(),
            kind         text NOT NULL CHECK (kind IN
                            ('revenge','overtrade','fomo','oversize','disposition')),
            severity     text NOT NULL CHECK (severity IN ('low','medium','high')),
            payload      jsonb NOT NULL,
            window_start timestamptz NOT NULL,
            window_end   timestamptz NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX bias_user_recent ON bias_events (user_id, detected_at DESC)"
    )

    # ----------------------------- backtest_runs ----------------------------
    op.execute(
        """
        CREATE TABLE backtest_runs (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            strategy_id   text NOT NULL,
            params        jsonb NOT NULL,
            symbol        text NOT NULL,
            timeframe     text NOT NULL,
            range_start   timestamptz NOT NULL,
            range_end     timestamptz NOT NULL,
            fees_bps      numeric NOT NULL DEFAULT 4,
            slippage_atr  numeric NOT NULL DEFAULT 0.05,
            seed          int,
            status        text NOT NULL CHECK (status IN ('running','done','error')),
            error_msg     text,
            metrics       jsonb,
            equity_curve  jsonb,
            created_at    timestamptz NOT NULL DEFAULT now(),
            finished_at   timestamptz
        )
        """
    )
    op.execute(
        "CREATE INDEX backtest_strategy_recent "
        "ON backtest_runs (strategy_id, created_at DESC)"
    )

    # ----------------------------- strategy_metrics -------------------------
    op.execute(
        """
        CREATE TABLE strategy_metrics (
            strategy_id   text PRIMARY KEY,
            last_run_id   uuid REFERENCES backtest_runs(id) ON DELETE SET NULL,
            n_runs        int NOT NULL DEFAULT 0,
            best_dsr      numeric,
            best_pbo      numeric,
            last_updated  timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS strategy_metrics CASCADE")
    op.execute("DROP TABLE IF EXISTS backtest_runs CASCADE")
    op.execute("DROP TABLE IF EXISTS bias_events CASCADE")
    op.execute("DROP TABLE IF EXISTS journal_trades CASCADE")
