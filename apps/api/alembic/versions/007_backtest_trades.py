"""F4: persistir trades individuales por backtest_run.

Revision ID: 007
Revises: 006
Create Date: 2026-05-05

Hasta hoy `apps/api/app/backtest/runner.py` calcula `trades: list[Trade]`
en memoria pero solo persiste `metrics` y `equity_curve` en `backtest_runs`.
Esto bloquea cualquier vista de "histograma real de R-multiples" o
"tu mejor/peor trade fue X" en el frontend, que ahora solo puede mostrar
agregados (win_rate, avg_win_R, avg_loss_R).

Esta migración añade `trades JSONB NOT NULL DEFAULT '[]'::jsonb` para
persistirlos. Cada item del array tiene la forma:

    {
        "entry_ts": "ISO8601",
        "exit_ts":  "ISO8601",
        "side":     "long",
        "entry_px": float,
        "exit_px":  float,
        "r_multiple": float,
        "pnl":      float,
        "bars_held": int,
        "exit_reason": "signal" | "stop"
    }

No añadimos GIN index porque no hay queries tipo `WHERE trades @> '[…]'`;
los `trades` se leen siempre junto con el run completo (1 SELECT por
detail). El INSERT del runner sobre 1000+ trades sigue siendo barato
porque PG comprime el JSONB inline.

Backfill: runs anteriores se quedan con `trades = '[]'`. El frontend
detecta el array vacío y muestra empty state ("re-ejecuta el backtest
para ver los trades individuales").
"""

from collections.abc import Sequence

from alembic import op

revision: str = "007"
down_revision: str | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE backtest_runs
        ADD COLUMN IF NOT EXISTS trades JSONB NOT NULL DEFAULT '[]'::jsonb
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE backtest_runs DROP COLUMN IF EXISTS trades")
