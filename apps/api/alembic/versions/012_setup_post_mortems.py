"""F5.5: setup_post_mortems — análisis terminal al cierre de un setup.

Revision ID: 012
Revises: 011
Create Date: 2026-05-11

Tabla terminal one-to-one con `journal_trades`. Cuando un setup toca SL o TP-all,
`post_mortem_dispatcher` ejecuta un agente independiente (`PostMortem` output)
que emite un veredicto estructurado, identifica factores que funcionaron/
fallaron desde `factor_snapshot` (migración 011), y persiste una lección
accionable.

¿Por qué separada de `setup_reviews`?
- **Idempotencia terminal**: UNIQUE(trade_id) — exactamente UN post-mortem
  por setup. Las reviews pueden acumular 6-10 por setup; las queries de un
  caso son MUY distintas (los review counts vs post-mortem outcome buckets).
- **Schema distinto**: post-mortems cargan `factor_verdicts` (qué factor
  funcionó/falló) y `entry_vs_exit_delta` (cambio en ScoreComponents entre
  entrada y salida). Reviews cargan `recommendation` (qué hacer ahora) que
  no aplica a un trade cerrado.
- **CHECK constraint distinto**: outcomes (win/loss/breakeven/partial_win)
  vs reviews (recommendation: hold/tighten_sl/...).

`outcome` clasificación:
- `win`            — r_multiple > 0.2 (cerró en TP con ganancia significativa)
- `loss`           — r_multiple <= 0
- `breakeven`      — 0 < r_multiple <= 0.2 (TP1 hit pero apenas)
- `partial_win`    — exit_reason='manual_close' con r > 0 sin todos los TPs

`exit_reason`:
- `sl_hit`         — stop loss tocado
- `tp_hit`         — todos los TPs tocados
- `manual_close`   — usuario cerró manualmente
- `time_stop`      — futuro: cierre por timeout

Cost/usage telemetry: `model_id`, `usage_tokens`, `cost_usd`, `prompt_version`
— mismos campos que setup_reviews para reusar tooling de observability.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "012"
down_revision: str | None = "011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE setup_post_mortems (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            trade_id            uuid NOT NULL UNIQUE
                                  REFERENCES journal_trades(id) ON DELETE CASCADE,
            user_id             text NOT NULL,
            outcome             text NOT NULL CHECK (outcome IN (
                                  'win', 'loss', 'breakeven', 'partial_win'
                                )),
            r_multiple          numeric NOT NULL,
            exit_reason         text NOT NULL CHECK (exit_reason IN (
                                  'sl_hit', 'tp_hit', 'manual_close', 'time_stop'
                                )),
            verdict             text NOT NULL CHECK (verdict IN (
                                  'thesis_held', 'thesis_broken',
                                  'execution_error', 'noise'
                                )),
            confidence_calibration text NOT NULL CHECK (confidence_calibration IN (
                                  'over', 'under', 'calibrated'
                                )),
            factor_verdicts     jsonb NOT NULL DEFAULT '{}'::jsonb,
            what_worked         jsonb NOT NULL DEFAULT '[]'::jsonb,
            what_failed         jsonb NOT NULL DEFAULT '[]'::jsonb,
            lesson_es           text NOT NULL,
            summary_es          text NOT NULL,
            counterfactual_es   text,
            entry_vs_exit_delta jsonb,
            citations           jsonb NOT NULL DEFAULT '[]'::jsonb,
            model_id            text NOT NULL,
            usage_tokens        jsonb,
            cost_usd            numeric,
            prompt_version      text,
            created_at          timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        "CREATE INDEX idx_post_mortems_user_created "
        "ON setup_post_mortems (user_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_post_mortems_outcome "
        "ON setup_post_mortems (user_id, outcome, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_post_mortems_verdict "
        "ON setup_post_mortems (user_id, verdict, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_post_mortems_verdict")
    op.execute("DROP INDEX IF EXISTS idx_post_mortems_outcome")
    op.execute("DROP INDEX IF EXISTS idx_post_mortems_user_created")
    op.execute("DROP TABLE IF EXISTS setup_post_mortems CASCADE")
