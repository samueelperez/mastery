"""F5.5+: drop redundant `summary_es` from setup_post_mortems.

Revision ID: 025
Revises: 024
Create Date: 2026-05-12

`setup_post_mortems.summary_es` siempre se popula con el mismo valor que
`lesson_es` (verificado: `dispatcher.py:221` ejecuta literal
`summary_es=pm.lesson_es`). Es la misma duplicación que la migración 015 ya
trató con `what_worked` / `what_failed` — la mantenemos siguiendo el mismo
patrón: drop + adjust insert path.

Backfill: ninguno. Los consumidores leen via repo helpers que pasan a usar
`lesson_es` para el campo summary en respuestas.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "025"
down_revision: str | None = "024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE setup_post_mortems DROP COLUMN IF EXISTS summary_es"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE setup_post_mortems "
        "ADD COLUMN summary_es text NOT NULL DEFAULT ''"
    )
