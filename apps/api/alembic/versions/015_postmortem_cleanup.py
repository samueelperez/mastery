"""F5.5+: cleanup de columnas redundantes en setup_post_mortems.

Revision ID: 015
Revises: 014
Create Date: 2026-05-12

Las columnas `what_worked` y `what_failed` (introducidas en migración 012)
son **copia literal** de `success_factors` y `failure_factors` que se
persisten también dentro de `factor_verdicts` JSONB (con verdict='worked'/
'failed' por factor). El dispatcher las populaba via:

    what_worked = pm.success_factors
    what_failed = pm.failure_factors

Ningún consumer downstream (tools del agente, endpoints, frontend) las usa
distinctamente — todos reconstruyen los lists desde `factor_verdicts` o
desde el output del agente. Mantenerlas duplica el storage cost y obliga a
sincronizarlas al renderizar.

Esta migración las DROP. La info no se pierde:
- `factor_verdicts JSONB` sigue conservando el dictionary `{factor_key:
  {value, verdict, delta}}` con verdict ∈ {worked, failed, neutral}.
- Para queries de tipo "qué factores fallan más", `factor_outcomes`
  denormalizada (migración 013) ya es la fuente canónica.

Mantenemos en su sitio:
- `counterfactual_es`: lo renderiza `PostMortemCard.tsx` en el detalle —
  valor humano aunque no entre al feedback loop del agente.
- `entry_vs_exit_delta`: lo consume el propio post_mortem_dispatcher al
  construir el user prompt del agente — telemetría operativa.

Backfill: ninguno necesario. Las columnas se borran y el código que las
escribía se elimina simultáneamente.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "015"
down_revision: str | None = "014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE setup_post_mortems
            DROP COLUMN IF EXISTS what_worked,
            DROP COLUMN IF EXISTS what_failed
        """
    )


def downgrade() -> None:
    # Re-añade las columnas con su shape original. NO restaura los datos
    # — quien necesite los lists debe reconstruirlos desde factor_verdicts.
    op.execute(
        """
        ALTER TABLE setup_post_mortems
            ADD COLUMN what_worked jsonb NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN what_failed jsonb NOT NULL DEFAULT '[]'::jsonb
        """
    )
