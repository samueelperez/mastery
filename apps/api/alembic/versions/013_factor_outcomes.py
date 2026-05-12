"""F5.5: factor_outcomes — tabla denormalizada para agregación rápida.

Revision ID: 013
Revises: 012
Create Date: 2026-05-11

Una fila por (trade_id, factor_name, factor_tf). Cuando un trade cierra,
`setup_repo.transition_status` fan-outs el `factor_snapshot` de
`journal_trades` a N filas aquí — una por cada factor deterministic (con su
timeframe) y una por cada tag semántico (factor_tf=NULL).

¿Por qué denormalizada y no materialized view o JSONB scan?

- A ~500 trades cerrados/año/usuario × ~12 factor rows ≈ 6k filas/año. Trivial.
- Queries de agregación (`SELECT factor_name, AVG(...) FROM factor_outcomes
  WHERE user_id=... GROUP BY factor_name`) corren en <10ms con índices vs
  ~100-500ms scaneando JSONB en `journal_trades` con jsonb_each.
- Mat-view serializa REFRESH globalmente (no per-user) y añade superficie
  admin. Las stats se piden bajo demanda en cada turno de chat — el path
  hot necesita ser plano.
- `journal_trades` no es hypertable Timescale (solo `ohlcv` lo es), así
  que continuous aggregates no aplican.

Idempotencia: UNIQUE(trade_id, factor_name, factor_tf). Si `transition_status`
se ejecuta dos veces (carrera), el segundo fan-out hace nada vía
ON CONFLICT DO NOTHING.

`is_holdout` denormalizado (copia de `journal_trades.is_holdout`): evita el
JOIN en la query hot de stats. Set una vez en fan-out.

Outcome classification: ver migración 012 (mismas reglas — win/loss/breakeven/
partial_win).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "013"
down_revision: str | None = "012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE factor_outcomes (
            id              bigserial PRIMARY KEY,
            trade_id        uuid NOT NULL
                              REFERENCES journal_trades(id) ON DELETE CASCADE,
            user_id         text NOT NULL,
            symbol          text NOT NULL,
            timeframe       text NOT NULL,
            factor_name     text NOT NULL,
            factor_tf       text,
            factor_kind     text NOT NULL CHECK (factor_kind IN (
                              'deterministic', 'semantic'
                            )),
            factor_value    numeric,
            factor_present  boolean NOT NULL,
            regime_label    text,
            r_multiple      numeric NOT NULL,
            outcome         text NOT NULL CHECK (outcome IN (
                              'win', 'loss', 'breakeven', 'partial_win'
                            )),
            is_holdout      boolean NOT NULL DEFAULT FALSE,
            closed_at       timestamptz NOT NULL,
            CONSTRAINT factor_outcomes_unique_per_trade
                UNIQUE (trade_id, factor_name, factor_tf)
        )
        """
    )

    # Path hot: get_factor_hit_rates filtra por (user_id, factor_name,
    # factor_kind, lookback). Ordenado por closed_at DESC para windowing
    # eficiente con LIMIT/lookback_days.
    op.execute(
        """
        CREATE INDEX idx_fo_user_factor
        ON factor_outcomes (user_id, factor_name, factor_kind, closed_at DESC)
        WHERE is_holdout = FALSE
        """
    )

    # EXT-2: stats segmentadas por régimen. Parcial-no-holdout.
    op.execute(
        """
        CREATE INDEX idx_fo_user_regime_factor
        ON factor_outcomes (user_id, regime_label, factor_name)
        WHERE is_holdout = FALSE
        """
    )

    # Para ON DELETE CASCADE y queries del estilo "todos los factores del trade X".
    op.execute("CREATE INDEX idx_fo_trade ON factor_outcomes (trade_id)")

    # EXT-4: bucket holdout para /holdout-performance — bucket pequeño, full scan.
    op.execute(
        """
        CREATE INDEX idx_fo_holdout
        ON factor_outcomes (user_id, factor_name, closed_at DESC)
        WHERE is_holdout = TRUE
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_fo_holdout")
    op.execute("DROP INDEX IF EXISTS idx_fo_trade")
    op.execute("DROP INDEX IF EXISTS idx_fo_user_regime_factor")
    op.execute("DROP INDEX IF EXISTS idx_fo_user_factor")
    op.execute("DROP TABLE IF EXISTS factor_outcomes CASCADE")
