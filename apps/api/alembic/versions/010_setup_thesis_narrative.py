"""F5+: persist full thesis narrative on agent_proposal setups.

Revision ID: 010
Revises: 009
Create Date: 2026-05-11

Originalmente `journal_trades.summary_text` recibe `idea.summary_es[:300]` —
es OK como descripción breve para listings, pero pierde la tesis completa que
el agente principal compuso al emitir el TradeIdea. Las TradeReviews post-
entry necesitan esa tesis para juzgar "¿se mantiene?" sin tener que re-
derivarla vía tools cada vez.

Añadimos:
- `summary_es_full TEXT`         — copy verbatim de `TradeIdea.summary_es`
                                    (≤1100 chars per model spec).
- `confluences   JSONB`          — list of {timeframe, bias, narrative}
                                    (citations descartadas — stale tras
                                    minutos del proposal).
- `scenarios     JSONB`          — list of {label, probability_pct,
                                    description, entry, stop_loss, target}.

Backfill: filas pre-existentes copian `summary_text` (300-char truncado) a
`summary_es_full` como mejor esfuerzo; `confluences` y `scenarios` quedan
en `[]` — perdidos definitivamente (no estaban persistidos).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "010"
down_revision: str | None = "009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE journal_trades
            ADD COLUMN summary_es_full TEXT,
            ADD COLUMN confluences jsonb NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN scenarios   jsonb NOT NULL DEFAULT '[]'::jsonb
        """
    )

    # Backfill: setups pre-existentes copian summary_text (que ya tiene
    # idea.summary_es[:300]) como fallback. Mejor que NULL para el agente.
    op.execute(
        """
        UPDATE journal_trades
        SET summary_es_full = summary_text
        WHERE source = 'agent_proposal'
          AND summary_es_full IS NULL
          AND summary_text IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE journal_trades
            DROP COLUMN IF EXISTS scenarios,
            DROP COLUMN IF EXISTS confluences,
            DROP COLUMN IF EXISTS summary_es_full
        """
    )
