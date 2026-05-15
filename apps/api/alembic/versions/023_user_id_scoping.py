"""Multi-tenant fix — add user_id to backtest_runs and strategy_metrics.

The audit found that `backtest_runs` and `strategy_metrics` (migration 002)
have no `user_id` column. Implications:

1. DSR cross-tenant leak: `n_runs` is global, so the more backtests user A
   runs, the harsher the deflation user B experiences (Bailey DSR penalises
   by trial count). User A's sweep contaminates user B's metrics.

2. Privacy leak: `GET /backtests` and `agent/tools/strategy_metrics.run_backtest`
   return runs of any user when called by any user.

3. The citation validator trusts `tool_name + run_id` as a receipt. Without
   user_id scoping, user A's chat can cite a run_id that exists but belongs
   to user B — the validator passes, but the "edge" is not the user's.

This migration:
- Adds `user_id text NOT NULL DEFAULT 'me'` to both tables (legacy 'me'
  default mirrors `journal_trades`/`bias_events` so existing rows survive).
- Repins `strategy_metrics.PRIMARY KEY` from `(strategy_id)` to
  `(strategy_id, user_id)`.
- Adds an index on `backtest_runs (user_id, strategy_id, created_at DESC)`
  for the per-user "latest runs" query.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "023"
down_revision: str | None = "022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # backtest_runs: add user_id + index
    op.execute(
        "ALTER TABLE backtest_runs ADD COLUMN user_id text NOT NULL DEFAULT 'me'"
    )
    op.execute(
        "CREATE INDEX backtest_runs_user_strategy_recent "
        "ON backtest_runs (user_id, strategy_id, created_at DESC)"
    )

    # strategy_metrics: add user_id, repin PK
    op.execute(
        "ALTER TABLE strategy_metrics ADD COLUMN user_id text NOT NULL DEFAULT 'me'"
    )
    op.execute(
        "ALTER TABLE strategy_metrics DROP CONSTRAINT strategy_metrics_pkey"
    )
    op.execute(
        "ALTER TABLE strategy_metrics "
        "ADD CONSTRAINT strategy_metrics_pkey PRIMARY KEY (strategy_id, user_id)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE strategy_metrics DROP CONSTRAINT strategy_metrics_pkey"
    )
    op.execute(
        "ALTER TABLE strategy_metrics "
        "ADD CONSTRAINT strategy_metrics_pkey PRIMARY KEY (strategy_id)"
    )
    op.execute("ALTER TABLE strategy_metrics DROP COLUMN user_id")
    op.execute("DROP INDEX IF EXISTS backtest_runs_user_strategy_recent")
    op.execute("ALTER TABLE backtest_runs DROP COLUMN user_id")
