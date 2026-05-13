"""Audit fix — prevent multi-approve / multi-reject race conditions.

The audit found that POST `/setups/{id}/approve` does its idempotency check
as a non-atomic SELECT-then-INSERT. Two concurrent clicks (user on web + on
mobile, or a Telegram button + a UI button at the same time) can both pass
the existence check and INSERT two `approved` rows. Same for `rejected_by_user`.

A partial UNIQUE index on `(trade_id, event)` constrained to those two event
kinds enforces at-most-one at the DB level. Other event kinds (proposed,
entry_hit, tp_hit, sl_hit, be_moved, trailing_updated, time_stopped,
review_generated) legitimately occur multiple times per trade and are not
affected.

Defensive cleanup: if the table already has duplicate approved/rejected_by_user
rows (impossible today but cheap insurance against race history), the index
creation would fail. We pre-delete duplicates keeping the OLDEST row per
(trade_id, event) — that's the row the original idempotency check would have
returned as "already exists".
"""

from collections.abc import Sequence

from alembic import op

revision: str = "021"
down_revision: str | None = "020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Step 1: clean up any pre-existing duplicates (oldest row wins).
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY trade_id, event
                       ORDER BY created_at ASC, id ASC
                   ) AS rn
            FROM setup_events
            WHERE event IN ('approved', 'rejected_by_user')
        )
        DELETE FROM setup_events
        WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
        """
    )

    # Step 2: enforce one approval and one rejection per setup at most.
    op.execute(
        """
        CREATE UNIQUE INDEX setup_events_unique_user_decision
        ON setup_events (trade_id, event)
        WHERE event IN ('approved', 'rejected_by_user')
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS setup_events_unique_user_decision")
