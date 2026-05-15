"""Security fix — prevent telegram_chat_id hijacking via UNIQUE index.

The audit found that `user_notification_settings.telegram_chat_id` has no
UNIQUE constraint. If two users bind the same chat (race or adversarial
re-bind with another user's leaked bind code), `_resolve_user_from_chat`
in `notifications/routes.py` does `LIMIT 1` over the duplicate rows and
returns a non-deterministic user_id. That user_id is then used to call
`approve_setup` / `reject_setup` — a path to take action on another user's
setup.

A partial UNIQUE index (WHERE telegram_chat_id IS NOT NULL) enforces
at-most-one user per Telegram chat. Repo logic must also explicitly
unbind a previous owner before binding to a new user (see
`notifications/repo.py::set_telegram_chat_id`).

Defensive cleanup before creating the index: if duplicates exist (they
shouldn't, but cheap insurance), keep the most recent linked row.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "022"
down_revision: str | None = "021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Step 1: clean any pre-existing duplicates (newest linked row wins).
    op.execute(
        """
        WITH ranked AS (
            SELECT user_id,
                   telegram_chat_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY telegram_chat_id
                       ORDER BY telegram_linked_at DESC NULLS LAST, user_id ASC
                   ) AS rn
            FROM user_notification_settings
            WHERE telegram_chat_id IS NOT NULL
        )
        UPDATE user_notification_settings AS uns
        SET telegram_chat_id = NULL,
            telegram_linked_at = NULL,
            updated_at = now()
        FROM ranked
        WHERE uns.user_id = ranked.user_id
          AND ranked.rn > 1
        """
    )

    # Step 2: enforce at-most-one user per Telegram chat.
    op.execute(
        """
        CREATE UNIQUE INDEX user_notification_settings_telegram_chat_id_idx
        ON user_notification_settings (telegram_chat_id)
        WHERE telegram_chat_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS user_notification_settings_telegram_chat_id_idx"
    )
