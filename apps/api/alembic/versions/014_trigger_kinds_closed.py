"""F5.5: extender setup_reviews.trigger_kind con setup_closed_sl/tp.

Revision ID: 014
Revises: 013
Create Date: 2026-05-11

Aunque los post-mortems viven en su propia tabla (`setup_post_mortems`,
migración 012), mantenemos el literal de trigger_kind consistente entre
ambos sistemas. El review_dispatcher histórico nunca emitirá un review
con estos kinds — son exclusivos del post_mortem_dispatcher — pero al
estar en el mismo dominio del CHECK constraint el código de telemetry y
analytics puede tratarlos uniformemente.

Cambios:
- Extender `setup_reviews.trigger_kind` CHECK con `'setup_closed_sl'` y
  `'setup_closed_tp'`.
- Sin cambios en `setup_events.event` (esa columna ya tiene `sl_hit` y
  `tp_hit` desde migración 009).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "014"
down_revision: str | None = "013"
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
            'setup_closed_sl', 'setup_closed_tp'
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
            'price_move', 'approaching_sl', 'regime_change'
        ))
        """
    )
