"""F5: post-entry trade reviews.

Revision ID: 009
Revises: 008
Create Date: 2026-05-11

Auto-reviews del agente sobre setups ACTIVE. Cuando el SetupRuntime detecta
entry_hit, tp_partial, movimiento de precio significativo, proximidad al SL,
o tiempo transcurrido desde entry, dispara un `review_agent` (secundario,
output_type=TradeReview) que emite un análisis estructurado con recomendación
accionable (hold / tighten_sl / partial_close / exit_now).

Cambios:

1. **Nueva tabla `setup_reviews`** (separada de `setup_events` para queryability
   — un setup activo puede acumular 6-10 reviews; las queries agregadas por
   `recommendation='exit_now'` justifican tabla propia con índices dedicados).

2. **Columnas en `journal_trades`** para cooldown y scheduling:
   - `last_review_at`        — último review emitido (cooldown gate).
   - `last_review_price`     — precio en ese momento (price-move guard).
   - `review_count`          — bound para coste runaway (cap 12 por setup).
   - `next_review_at`        — driver del time scheduler (`WHERE next_review_at <= now()`).
   - `last_review_attempt_at`— backoff si OpenRouter está caído (NO bumpea
                              cooldown porque no llegó a persistir review).

3. **Extender `setup_events.event` CHECK** con `'review_generated'` — la review
   también deja un evento en el timeline (payload mínimo: review_id + summary
   + recommendation; el detalle completo vive en `setup_reviews`).

4. **Backfill**: setups already-active al deploy → `next_review_at =
   entry_hit_at + interval '4 hours'`. Los pending no necesitan backfill
   (next_review_at se setea en el hook de entry_hit).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "009"
down_revision: str | None = "008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- 1. Cooldown + scheduling columns on journal_trades ------------------
    op.execute(
        """
        ALTER TABLE journal_trades
            ADD COLUMN last_review_at           timestamptz,
            ADD COLUMN last_review_price        numeric,
            ADD COLUMN review_count             int NOT NULL DEFAULT 0,
            ADD COLUMN next_review_at           timestamptz,
            ADD COLUMN last_review_attempt_at   timestamptz
        """
    )

    # Partial index for the time-based scheduler. Only active+agent_proposal
    # setups with a scheduled review_at can match — keeps the index tiny.
    op.execute(
        """
        CREATE INDEX idx_journal_trades_review_due
        ON journal_trades (next_review_at)
        WHERE status = 'active'
          AND source = 'agent_proposal'
          AND next_review_at IS NOT NULL
        """
    )

    # --- 2. setup_reviews ----------------------------------------------------
    op.execute(
        """
        CREATE TABLE setup_reviews (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            trade_id        uuid NOT NULL
                              REFERENCES journal_trades(id) ON DELETE CASCADE,
            user_id         text NOT NULL,
            trigger_kind    text NOT NULL CHECK (trigger_kind IN (
                              'entry_hit', 'tp_partial', 'time_elapsed',
                              'price_move', 'approaching_sl', 'regime_change'
                            )),
            trigger_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
            current_state   text NOT NULL CHECK (current_state IN (
                              'on_track', 'at_risk', 'reversing'
                            )),
            recommendation  text NOT NULL CHECK (recommendation IN (
                              'hold', 'tighten_sl', 'partial_close', 'exit_now'
                            )),
            summary         text NOT NULL,
            rationale       text NOT NULL,
            citations       jsonb NOT NULL DEFAULT '[]'::jsonb,
            price_at_review numeric NOT NULL,
            model_id        text NOT NULL,
            usage_tokens    jsonb,
            cost_usd        numeric,
            prompt_version  text,
            created_at      timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_setup_reviews_trade "
        "ON setup_reviews (trade_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_setup_reviews_user "
        "ON setup_reviews (user_id, created_at DESC)"
    )

    # --- 3. Extend setup_events.event CHECK ----------------------------------
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

    # --- 4. Backfill: schedule first time-based review for setups already active
    # 4h después del entry_hit. Si entry_hit_at + 4h < now(), el scheduler
    # los recoge en su próximo tick (5 min) — eso es lo deseado.
    op.execute(
        """
        UPDATE journal_trades
        SET next_review_at = entry_hit_at + interval '4 hours'
        WHERE status = 'active'
          AND source = 'agent_proposal'
          AND entry_hit_at IS NOT NULL
          AND next_review_at IS NULL
        """
    )


def downgrade() -> None:
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
    op.execute("DROP INDEX IF EXISTS idx_setup_reviews_user")
    op.execute("DROP INDEX IF EXISTS idx_setup_reviews_trade")
    op.execute("DROP TABLE IF EXISTS setup_reviews CASCADE")
    op.execute("DROP INDEX IF EXISTS idx_journal_trades_review_due")
    op.execute(
        """
        ALTER TABLE journal_trades
            DROP COLUMN IF EXISTS last_review_attempt_at,
            DROP COLUMN IF EXISTS next_review_at,
            DROP COLUMN IF EXISTS review_count,
            DROP COLUMN IF EXISTS last_review_price,
            DROP COLUMN IF EXISTS last_review_at
        """
    )
