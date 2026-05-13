"""F4: pre-entry invalidation conditions + wall-clock expiry.

Revision ID: 008
Revises: 007
Create Date: 2026-05-11

Auto-invalidation of pending setups. While a setup is `status='pending'`,
`SetupRuntime` evaluates a list of `invalidation_conditions` (RuleSpec-shaped
DSL reusing `app.alerts.dsl`) on each candle close, plus an optional wall-clock
`expires_at` timestamp. The first condition to fire transitions the setup
`pending → cancelled` with `setup_events.event = 'invalidated'`.

Three sets of changes:

1. **Rename** `invalidation_px` → `stop_loss_px` on `journal_trades`. The
   freed word `invalidation` is reserved for the pre-entry concept introduced
   here. The Pydantic model `TradeIdea.invalidation` → `TradeIdea.stop_loss`
   changed in the same release.

2. **New columns** on `journal_trades`:
   - `invalidation_conditions jsonb NOT NULL DEFAULT '[]'` — list of
     `InvalidationCondition` dicts. Empty list = no auto-invalidation.
   - `expires_at timestamptz` — wall-clock expiry (UTC). NULL = no expiry.
   - `expires_at_rationale text` — agent's reason for the expiry.
   - `expires_at_citations jsonb` — `ToolCitation[]` backing the rationale.
   - `invalidated_at timestamptz` — set when the setup auto-cancelled
     (distinct from `closed_at`, used for status='closed' setups).

3. **Extend `setup_events.event` CHECK** to include `'invalidated'`. The
   existing `'cancelled'` event stays for manual user-cancels; the new
   event is reserved for auto-trigger via condition / expiry.

Existing pending/active rows: default values are "no conditions, no expiry"
— exactly the prior behavior. Nothing to backfill.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "008"
down_revision: str | None = "007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- 1. Rename SL column. Data preserved in place by RENAME. ---
    op.execute(
        """
        ALTER TABLE journal_trades RENAME COLUMN invalidation_px TO stop_loss_px
        """
    )

    # --- 2. New columns for pre-entry invalidation. ---
    op.execute(
        """
        ALTER TABLE journal_trades
            ADD COLUMN invalidation_conditions jsonb NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN expires_at timestamptz,
            ADD COLUMN expires_at_rationale text,
            ADD COLUMN expires_at_citations jsonb,
            ADD COLUMN invalidated_at timestamptz
        """
    )

    # Cheap pre-filter for the wall-clock sweeper. Partial index keeps
    # storage small and the WHERE clause makes the planner pick it for
    # `WHERE status='pending' AND expires_at IS NOT NULL AND expires_at <= now()`.
    op.execute(
        """
        CREATE INDEX idx_journal_trades_expires
        ON journal_trades (expires_at)
        WHERE status = 'pending' AND expires_at IS NOT NULL
        """
    )

    # --- 3. Extend setup_events.event CHECK. ---
    # Postgres named the inline-CHECK from migration 005 `setup_events_event_check`.
    op.execute("ALTER TABLE setup_events DROP CONSTRAINT setup_events_event_check")
    op.execute(
        """
        ALTER TABLE setup_events ADD CONSTRAINT setup_events_event_check
        CHECK (event IN (
            'proposed', 'entry_hit', 'tp_hit', 'sl_hit',
            'expired', 'manual_close', 'cancelled', 'invalidated'
        ))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE setup_events DROP CONSTRAINT setup_events_event_check")
    op.execute(
        """
        ALTER TABLE setup_events ADD CONSTRAINT setup_events_event_check
        CHECK (event IN (
            'proposed', 'entry_hit', 'tp_hit',
            'sl_hit', 'expired', 'manual_close', 'cancelled'
        ))
        """
    )
    op.execute("DROP INDEX IF EXISTS idx_journal_trades_expires")
    op.execute(
        """
        ALTER TABLE journal_trades
            DROP COLUMN IF EXISTS invalidated_at,
            DROP COLUMN IF EXISTS expires_at_citations,
            DROP COLUMN IF EXISTS expires_at_rationale,
            DROP COLUMN IF EXISTS expires_at,
            DROP COLUMN IF EXISTS invalidation_conditions
        """
    )
    op.execute(
        "ALTER TABLE journal_trades RENAME COLUMN stop_loss_px TO invalidation_px"
    )
