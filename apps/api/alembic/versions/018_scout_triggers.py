"""C.1 Scout triggers — extend alert_rules with `is_scout_trigger` flag.

When `is_scout_trigger=TRUE`, a rule match does NOT publish to the user's
alerts channel as a regular alert. Instead, the alerts runtime hands the
match off to the ScoutDispatcher, which:

  - Applies the B.3 cooldown (`should_pause_scout`) — recent SL streaks pause
    the scout for the user/symbol.
  - Applies C.2 scout discipline (rate limits + dedup + quality floor).
  - Invokes the main Agent (`get_agent()`) with a synthetic user message
    framed as "scanner rule R matched on {symbol}@{tf}; evaluate setup".
  - If the agent emits a valid TradeIdea, persists it via
    `insert_setup_from_idea` and the runtime watches it like any other.

The column is nullable-default `FALSE` so existing rules continue as
human-facing alerts. Set per-rule via the alerts CRUD endpoint.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "018"
down_revision: str | None = "017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE alert_rules
            ADD COLUMN is_scout_trigger boolean NOT NULL DEFAULT false
        """
    )
    # Partial index speeds up the scout-only sweep when most rules are
    # human-facing alerts.
    op.execute(
        "CREATE INDEX alert_rules_scout ON alert_rules (user_id, enabled) "
        "WHERE is_scout_trigger = true"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS alert_rules_scout")
    op.execute("ALTER TABLE alert_rules DROP COLUMN IF EXISTS is_scout_trigger")
