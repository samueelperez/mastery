"""F4: índice partial para agregados de winrate por setup_tag.

Revision ID: 006
Revises: 005
Create Date: 2026-05-05

`winrate_by_setup_tag()` (apps/api/app/storage/setup_repo.py) ejecuta:

    SELECT setup_tag, COUNT(*), AVG(r_multiple), MAX(closed_at)
    FROM journal_trades
    WHERE user_id=:uid AND status='closed' AND setup_tag IS NOT NULL
    GROUP BY setup_tag

Migrations previas dejaron `journal_lookup (user_id, mode, regime, setup_tag)`
(002:64) que cubre filtros por mode/regime pero NO por status. Con journal de
1000+ filas el GROUP BY hace seq scan completo cada vez que `/research/strategies`
recarga. Este índice partial:

- Reduce ~50% el tamaño del índice (status='closed' es un subset del journal).
- Cubre las cuatro columnas referenciadas → permite Index Only Scan en el GROUP BY.
- WHERE setup_tag IS NOT NULL filtra antes de indexar (cero overhead por NULLs).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX idx_journal_trades_closed_setup
        ON journal_trades (user_id, setup_tag, r_multiple, closed_at)
        WHERE status = 'closed' AND setup_tag IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_journal_trades_closed_setup")
