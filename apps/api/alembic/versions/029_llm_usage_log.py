"""LLM usage log — unified audit table for every Claude/OpenRouter
invocation (chat, scout, review, post-mortem, audit).

Until now we tracked LLM cost per-source: ``setup_reviews.cost_usd`` and
``setup_post_mortems.cost_usd``. The main chat and scout paths had no
persistent record at all — only Prometheus counters. The audit table
introduced here lets ops join cost to user / model / source in one place
without UNIONing per-source tables.

Schema is intentionally narrow:

- ``source`` is a free-form bounded enum (CHECK at the application layer
  rather than DB-side so adding a new agent kind in M2 doesn't require a
  migration).
- ``usage_tokens`` is jsonb so the exact key set can evolve with
  pydantic-ai's API without schema churn.
- ``request_id`` is a correlation id (uuid string) the producer mints so
  rows in this table can be joined with structured logs.

Reviewer + post-mortem keep their per-source cost fields for backwards
compat with existing dashboards; the dispatcher writes both places.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "029"
down_revision: str | None = "028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE llm_usage_log (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id         text NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            source          text NOT NULL,
            model_id        text NOT NULL,
            usage_tokens    jsonb,
            cost_usd        numeric(10, 6),
            request_id      text
        )
        """
    )
    op.execute(
        "CREATE INDEX llm_usage_log_user_ts_idx "
        "ON llm_usage_log (user_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX llm_usage_log_source_ts_idx "
        "ON llm_usage_log (source, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS llm_usage_log CASCADE")
