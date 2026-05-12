"""C.3 — User notification settings + setup approval/reject events.

Adds the persistence rails for human-in-loop approval of scout-proposed
setups. Live Telegram bot + web push integration ships later; this migration
gives us:

  - `user_notification_settings` — per-user Telegram chat_id + web push
    subscription list. Separate from BetterAuth's `user` table so we don't
    need to fork the auth schema.
  - Two new `setup_events.event` kinds: `approved`, `rejected_by_user` —
    the audit trail of human decisions on scout proposals.

The endpoint `POST /setups/{id}/approve` and `/reject` (added in
`app/api/setups.py`) writes these events and transitions the setup
appropriately.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "019"
down_revision: str | None = "018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE user_notification_settings (
            user_id              text PRIMARY KEY,
            telegram_chat_id     text,
            telegram_linked_at   timestamptz,
            webpush_subscriptions jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at           timestamptz NOT NULL DEFAULT now(),
            updated_at           timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute("ALTER TABLE setup_events DROP CONSTRAINT setup_events_event_check")
    op.execute(
        """
        ALTER TABLE setup_events ADD CONSTRAINT setup_events_event_check
        CHECK (event IN (
            'proposed', 'entry_hit', 'tp_hit', 'sl_hit',
            'expired', 'manual_close', 'cancelled', 'invalidated',
            'review_generated',
            'be_moved', 'trailing_updated', 'time_stopped',
            'approved', 'rejected_by_user'
        ))
        """
    )


def downgrade() -> None:
    # Defensive cleanup BEFORE re-narrowing the CHECK: otherwise existing
    # rows with the new event kinds violate the constraint and the downgrade
    # fails partway through. Audit-found edge case: if a fresh database goes
    # through upgrade → user clicks Approve → downgrade, the existing
    # `approved` event would block the migration. Same for `rejected_by_user`.
    op.execute(
        "DELETE FROM setup_events "
        "WHERE event IN ('approved', 'rejected_by_user')"
    )
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
    op.execute("DROP TABLE IF EXISTS user_notification_settings CASCADE")
