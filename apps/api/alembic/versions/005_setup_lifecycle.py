"""F4 prep: setup lifecycle (auto-save TradeIdea + status tracking).

Revision ID: 005
Revises: 004
Create Date: 2026-05-05

Extiende `journal_trades` para soportar setups propuestos por el agente que
viven a lo largo del tiempo (pending → active → closed) más allá de los
trades manuales/post-mortem que el schema soportaba en F2.

- `status`: ciclo de vida ('pending', 'active', 'closed', 'cancelled').
- `source`: orígen del registro ('agent_proposal' = auto-save desde TradeIdea,
  'manual_log' = log_trade tool del agente, 'paper'/'live' = futuro F5,
  'csv_import' = bulk).
- `invalidation_px`: separado de `exit_px` (price real al cerrar). Útil
  para distinguir el SL planificado del precio real de salida.
- `targets`: jsonb [{label, price, hit_at?}] — el watcher marca hit_at en
  cada cierre de candle relevante.
- `confidence`: 'low'|'medium'|'high' del TradeIdea original.
- `proposed_at`/`entry_hit_at`/`closed_at`: timeline.
- `dedup_hash`: idempotencia para auto-save (refinamientos del agente sobre
  el mismo setup no duplican).

Datos pre-existentes (mode='manual_log' con trade cerrado) se migran a
status='closed', source='manual_log'.

Tabla nueva `setup_events` para audit trail de transiciones (proposed,
entry_hit, sl_hit, tp_hit, expired, manual_close).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ----------------------------- journal_trades extensions -----------------
    op.execute(
        """
        ALTER TABLE journal_trades
            ADD COLUMN status text NOT NULL DEFAULT 'closed'
                CHECK (status IN ('pending', 'active', 'closed', 'cancelled')),
            ADD COLUMN source text NOT NULL DEFAULT 'manual_log'
                CHECK (source IN ('manual_log', 'agent_proposal',
                                  'paper', 'live', 'csv_import')),
            ADD COLUMN invalidation_px numeric,
            ADD COLUMN targets jsonb NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN confidence text
                CHECK (confidence IS NULL OR confidence IN ('low', 'medium', 'high')),
            ADD COLUMN proposed_at timestamptz,
            ADD COLUMN entry_hit_at timestamptz,
            ADD COLUMN closed_at timestamptz,
            ADD COLUMN dedup_hash text
        """
    )

    # Backfill: trades pre-existentes (mode='manual_log') se quedan closed
    # con su trade_ts como closed_at. Coherente con que ya tenían exit_px.
    op.execute(
        """
        UPDATE journal_trades
        SET source = mode,
            closed_at = trade_ts
        WHERE status = 'closed'
        """
    )

    # Lookup rápido del watcher: setups abiertos por user+symbol.
    op.execute(
        """
        CREATE INDEX idx_journal_trades_open
        ON journal_trades (user_id, symbol, timeframe)
        WHERE status IN ('pending', 'active')
        """
    )

    # Idempotencia del auto-save: el mismo (user, dedup_hash) no se duplica.
    # NULL hashes (logs manuales antiguos) no entran al unique index.
    op.execute(
        """
        CREATE UNIQUE INDEX idx_journal_trades_dedup
        ON journal_trades (user_id, dedup_hash)
        WHERE dedup_hash IS NOT NULL
        """
    )

    # ----------------------------- setup_events ------------------------------
    op.execute(
        """
        CREATE TABLE setup_events (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            trade_id    uuid NOT NULL
                          REFERENCES journal_trades(id) ON DELETE CASCADE,
            event       text NOT NULL CHECK (event IN
                          ('proposed', 'entry_hit', 'tp_hit',
                           'sl_hit', 'expired', 'manual_close', 'cancelled')),
            candle_ts   timestamptz NOT NULL,
            payload     jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_setup_events_trade "
        "ON setup_events (trade_id, candle_ts)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS setup_events CASCADE")
    op.execute("DROP INDEX IF EXISTS idx_journal_trades_dedup")
    op.execute("DROP INDEX IF EXISTS idx_journal_trades_open")
    op.execute(
        """
        ALTER TABLE journal_trades
            DROP COLUMN IF EXISTS dedup_hash,
            DROP COLUMN IF EXISTS closed_at,
            DROP COLUMN IF EXISTS entry_hit_at,
            DROP COLUMN IF EXISTS proposed_at,
            DROP COLUMN IF EXISTS confidence,
            DROP COLUMN IF EXISTS targets,
            DROP COLUMN IF EXISTS invalidation_px,
            DROP COLUMN IF EXISTS source,
            DROP COLUMN IF EXISTS status
        """
    )
