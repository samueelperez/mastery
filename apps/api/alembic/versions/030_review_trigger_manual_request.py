"""Add 'manual_request' to setup_reviews.trigger_kind CHECK constraint.

Enables a manual analyze-now button in the UI (POST /setups/{id}/analyze)
to dispatch a review without waiting for the automatic triggers
(entry_hit, tp_partial, time_elapsed, price_move, approaching_sl,
regime_change, setup_closed_sl, setup_closed_tp).

The constraint is the only DB-side guard for trigger_kind — the
application TriggerKind Literal in app/agent/models.py is updated in
the same commit.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "030"
down_revision: str | None = "029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE setup_reviews DROP CONSTRAINT setup_reviews_trigger_kind_check"
    )
    op.execute(
        """
        ALTER TABLE setup_reviews ADD CONSTRAINT setup_reviews_trigger_kind_check
        CHECK (trigger_kind IN (
            'entry_hit', 'tp_partial', 'time_elapsed',
            'price_move', 'approaching_sl', 'regime_change',
            'setup_closed_sl', 'setup_closed_tp',
            'manual_request'
        ))
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE setup_reviews DROP CONSTRAINT setup_reviews_trigger_kind_check"
    )
    op.execute(
        """
        ALTER TABLE setup_reviews ADD CONSTRAINT setup_reviews_trigger_kind_check
        CHECK (trigger_kind IN (
            'entry_hit', 'tp_partial', 'time_elapsed',
            'price_move', 'approaching_sl', 'regime_change',
            'setup_closed_sl', 'setup_closed_tp'
        ))
        """
    )
