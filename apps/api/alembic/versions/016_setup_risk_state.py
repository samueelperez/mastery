"""B.1 Risk Manager — risk_state JSONB + extended setup_events kinds.

Adds:
- `journal_trades.risk_state jsonb DEFAULT '{}'::jsonb` — runtime state for the
  RiskManager: breakeven_moved bool/at, trailing_active bool, trailing_sl,
  time_stopped bool. Read by the manager on each candle close to keep its
  actions idempotent (don't re-move BE on every candle once it's been moved).
- `setup_events.event` CHECK extended with `be_moved`, `trailing_updated`,
  `time_stopped`. Each is the audit trail of a single RiskManager action.

Note: the plan documented this as migration 017 but the next free number is
016 (014 trigger_kinds_closed → 015 postmortem_cleanup → next is 016).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "016"
down_revision: str | None = "015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE journal_trades
            ADD COLUMN risk_state jsonb NOT NULL DEFAULT '{}'::jsonb
        """
    )

    # Note: `review_generated` was added by migration 009 — keep it in the
    # list so existing rows don't violate the new constraint.
    op.execute("ALTER TABLE setup_events DROP CONSTRAINT setup_events_event_check")
    op.execute(
        """
        ALTER TABLE setup_events ADD CONSTRAINT setup_events_event_check
        CHECK (event IN (
            'proposed', 'entry_hit', 'tp_hit', 'sl_hit',
            'expired', 'manual_close', 'cancelled', 'invalidated',
            'review_generated',
            'be_moved', 'trailing_updated', 'time_stopped'
        ))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE setup_events DROP CONSTRAINT setup_events_event_check")
    op.execute(
        """
        ALTER TABLE setup_events ADD CONSTRAINT setup_events_event_check
        CHECK (event IN (
            'proposed', 'entry_hit', 'tp_hit', 'sl_hit',
            'expired', 'manual_close', 'cancelled', 'invalidated',
            'review_generated'
        ))
        """
    )
    op.execute("ALTER TABLE journal_trades DROP COLUMN risk_state")
