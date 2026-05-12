"""F5.5: factor_snapshot + mfe_mae + is_holdout en journal_trades.

Revision ID: 011
Revises: 010
Create Date: 2026-05-11

Tres adiciones a `journal_trades` que habilitan el sistema de aprendizaje
post-mortem:

1. **`factor_snapshot jsonb`** — captura los `ScoreComponents` deterministic
   que el scorer de confluencia computa al proponer el setup, más tags
   semánticos opcionales que el agente puede adjuntar, más contexto del
   mercado en el momento de creación.

   Shape (version=1):
       {
         "version": 1,
         "captured_at": "...",
         "deterministic": {
           "by_tf": {"1h": {...}, "4h": {...}}, "aggregate_bias": "...",
           "aggregate_agreement_pct": float
         },
         "semantic_tags": ["lvn_support", "fvg_fill"],
         "context": {"regime_label": "...", "atr_pct_1h": float, ...}
       }

   El scorer se ejecuta UNA vez al crear el setup y se persiste; las stats
   históricas se agregan por las claves estructurales (`ema_stack@1h`,
   `rsi@4h`, etc.) en `factor_outcomes` (migración 013).

2. **`mfe_mae jsonb`** (EXT-3) — escaneado en `post_mortem_dispatcher` antes
   de invocar al agente. Maximum Favorable/Adverse Excursion en R-units desde
   `entry_hit_at` hasta `closed_at`. Responde "¿mi SL está demasiado ajustado?"
   y "¿mis TP dejan dinero en la mesa?".

   Shape:
       {"mfe_r": float, "mae_r": float, "mfe_at": ts, "mae_at": ts,
        "time_to_mfe_h": float, "time_to_mae_h": float,
        "exit_efficiency_pct": float}

3. **`is_holdout boolean`** (EXT-4) — split deterministic por hash en
   `transition_status` al cerrar. Trades holdout NUNCA entran al preamble ni
   a `get_factor_hit_rates`; sólo se consultan vía
   `GET /api/journal/holdout-performance` para detectar overfit del feedback
   loop sobre su propio histórico.

Backfill: NULL en factor_snapshot/mfe_mae para filas pre-existentes (forward-
only para post-mortems). `is_holdout` defaultea a FALSE — los trades antiguos
quedan in-sample por defecto; el split sólo aplica a cierres futuros.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "011"
down_revision: str | None = "010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE journal_trades
            ADD COLUMN factor_snapshot jsonb,
            ADD COLUMN mfe_mae         jsonb,
            ADD COLUMN is_holdout      boolean NOT NULL DEFAULT FALSE
        """
    )

    # GIN sobre semantic_tags — queries del tipo "trades con tag lvn_support".
    op.execute(
        """
        CREATE INDEX idx_journal_trades_semantic_tags_gin
        ON journal_trades USING gin ((factor_snapshot -> 'semantic_tags'))
        """
    )

    # Parcial sobre regime_label para queries de stats segmentadas (EXT-2).
    # Sólo trades cerrados — pending/active no contribuyen a aprendizaje.
    op.execute(
        """
        CREATE INDEX idx_journal_trades_regime_closed
        ON journal_trades ((factor_snapshot -> 'context' ->> 'regime_label'))
        WHERE status = 'closed'
        """
    )

    # Parcial sobre is_holdout=TRUE — bucket pequeño (~15% de cierres), queries
    # de monitoring (`/holdout-performance`) lo escanean entero.
    op.execute(
        """
        CREATE INDEX idx_journal_trades_holdout
        ON journal_trades (closed_at DESC)
        WHERE is_holdout = TRUE AND status = 'closed'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_journal_trades_holdout")
    op.execute("DROP INDEX IF EXISTS idx_journal_trades_regime_closed")
    op.execute("DROP INDEX IF EXISTS idx_journal_trades_semantic_tags_gin")
    op.execute(
        """
        ALTER TABLE journal_trades
            DROP COLUMN IF EXISTS is_holdout,
            DROP COLUMN IF EXISTS mfe_mae,
            DROP COLUMN IF EXISTS factor_snapshot
        """
    )
