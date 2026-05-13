"""Relax `liquidation_provider_weights.weight` lower bound to 0.0.

Revision ID: 028
Revises: 027
Create Date: 2026-05-13

The original constraint (migration 026) required `weight >= 0.10`, but
that's the floor on the RAW agreement rate, not on the stored weight.
After per-cell normalization, a provider at floor + another at rate=1.0
yields 0.10/1.10 ≈ 0.091 — a legitimate stored value that the old
constraint rejected.

This migration changes the CHECK to `weight >= 0.0 AND weight <= 1.0`,
matching the pydantic model and the calibration math.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "028"
down_revision: str | None = "027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "prov_weights_floor",
        "liquidation_provider_weights",
        type_="check",
    )
    op.create_check_constraint(
        "prov_weights_floor",
        "liquidation_provider_weights",
        "weight >= 0.0 AND weight <= 1.0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "prov_weights_floor",
        "liquidation_provider_weights",
        type_="check",
    )
    op.create_check_constraint(
        "prov_weights_floor",
        "liquidation_provider_weights",
        "weight >= 0.10 AND weight <= 1.0",
    )
