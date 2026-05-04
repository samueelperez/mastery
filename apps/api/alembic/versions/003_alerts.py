"""F3: alert_rules + alert_events + bias_events_high NOTIFY trigger.

Revision ID: 003
Revises: 002
Create Date: 2026-05-04

The runtime evaluator (`app/alerts/runtime.py`) subscribes to Valkey market
channels and evaluates active rules on `is_closed=True` candles. The trigger
on `bias_events` raises a Postgres NOTIFY whenever a high-severity bias is
inserted, so the evaluator can promote it to an alert_event without polling.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ----------------------------- alert_rules ------------------------------
    op.execute(
        """
        CREATE TABLE alert_rules (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id         text NOT NULL DEFAULT 'me',
            name            text NOT NULL,
            spec            jsonb NOT NULL,
            enabled         boolean NOT NULL DEFAULT true,
            cooldown_s      int NOT NULL DEFAULT 3600 CHECK (cooldown_s >= 0),
            last_fired_at   timestamptz,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX alert_rules_lookup ON alert_rules (user_id, enabled)")
    # Partial index speeds up the runtime's per-tick lookup ("which rules care
    # about BTCUSDT 4h closes?"). Filtered to enabled=true so disabled rules
    # never enter the eval path.
    op.execute(
        """
        CREATE INDEX alert_rules_topic
        ON alert_rules ((spec->>'symbol'), (spec->>'timeframe'))
        WHERE enabled = true
        """
    )

    # ----------------------------- alert_events -----------------------------
    op.execute(
        """
        CREATE TABLE alert_events (
            id          bigserial PRIMARY KEY,
            user_id     text NOT NULL DEFAULT 'me',
            rule_id     uuid REFERENCES alert_rules(id) ON DELETE SET NULL,
            kind        text NOT NULL CHECK (kind IN ('rule_match','bias_promoted')),
            severity    text NOT NULL CHECK (severity IN ('low','medium','high')),
            fired_at    timestamptz NOT NULL DEFAULT now(),
            snapshot    jsonb NOT NULL,
            seen_at     timestamptz
        )
        """
    )
    op.execute(
        "CREATE INDEX alert_events_user_recent "
        "ON alert_events (user_id, fired_at DESC)"
    )
    op.execute(
        """
        CREATE INDEX alert_events_unread
        ON alert_events (user_id, fired_at DESC)
        WHERE seen_at IS NULL
        """
    )

    # ----------- NOTIFY bridge: bias_events(severity='high') → alerts -------
    # The runtime LISTENs for this notification channel and inserts an
    # alert_events(kind='bias_promoted'). Trigger payload carries the bias_event
    # id so the evaluator can join back if it needs the full payload.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION notify_bias_high() RETURNS trigger AS $$
        BEGIN
            IF NEW.severity = 'high' THEN
                PERFORM pg_notify(
                    'bias_events_high',
                    json_build_object(
                        'bias_event_id', NEW.id,
                        'user_id', NEW.user_id,
                        'kind', NEW.kind,
                        'detected_at', NEW.detected_at
                    )::text
                );
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER bias_events_notify_high
        AFTER INSERT ON bias_events
        FOR EACH ROW
        EXECUTE FUNCTION notify_bias_high()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS bias_events_notify_high ON bias_events")
    op.execute("DROP FUNCTION IF EXISTS notify_bias_high()")
    op.execute("DROP TABLE IF EXISTS alert_events CASCADE")
    op.execute("DROP TABLE IF EXISTS alert_rules CASCADE")
