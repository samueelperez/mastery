"""Blocker 1 — separate `scout_proposal` source so SetupRuntime can require
explicit user approval before activating scout-originated setups.

Without this, scout-originated setups would auto-transition pending → active
on entry hit just like any chat-originated TradeIdea, bypassing the
human-in-loop intent of C.3. After this migration:

  - `source = 'agent_proposal'` → chat-initiated, user explicitly asked,
    no extra approval gate (entry hit activates normally).
  - `source = 'scout_proposal'` → autonomous scout dispatch, requires
    `setup_events.event = 'approved'` before SetupRuntime activates.

Existing rows are untouched (all current proposals are agent-initiated).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "020"
down_revision: str | None = "019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Migration 005 named the inline CHECK `journal_trades_source_check`.
    op.execute(
        "ALTER TABLE journal_trades DROP CONSTRAINT journal_trades_source_check"
    )
    op.execute(
        """
        ALTER TABLE journal_trades ADD CONSTRAINT journal_trades_source_check
        CHECK (source IN (
            'manual_log', 'agent_proposal', 'scout_proposal',
            'paper', 'live', 'csv_import'
        ))
        """
    )


def downgrade() -> None:
    # Re-tag any scout rows back to agent_proposal so the previous CHECK passes.
    op.execute(
        "UPDATE journal_trades SET source = 'agent_proposal' "
        "WHERE source = 'scout_proposal'"
    )
    op.execute(
        "ALTER TABLE journal_trades DROP CONSTRAINT journal_trades_source_check"
    )
    op.execute(
        """
        ALTER TABLE journal_trades ADD CONSTRAINT journal_trades_source_check
        CHECK (source IN (
            'manual_log', 'agent_proposal', 'paper', 'live', 'csv_import'
        ))
        """
    )
