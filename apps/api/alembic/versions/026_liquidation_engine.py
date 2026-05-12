"""Liquidation heatmap engine — buckets, addresses, agreement log, weights.

Revision ID: 026
Revises: 025
Create Date: 2026-05-12

Cerebro 1 (Liquidation Heatmap Engine) — Day 1 schema. Adds four tables:

- `liquidation_buckets`: per-source persisted heatmap history.
- `hyperliquid_known_addresses`: address universe bootstrap for Provider B.
- `liquidation_agreement_log`: drives the M2 weight calibration decision.
- `liquidation_provider_weights`: output of `calibration.compute_provider_weights`.

Note: this migration is numbered 026 (not 025 as the original spec
`docs/specs/liquidation/01_MODELS_AND_SCHEMA.md` indicated). Slot 025 is
already occupied by `025_postmortem_drop_summary_es`. Spec 01 updated in the
same PR.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "026"
down_revision: str | None = "025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # liquidation_buckets — persisted heatmap history
    # -----------------------------------------------------------------------
    op.create_table(
        "liquidation_buckets",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Text, nullable=False),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("timeframe", sa.Text, nullable=False),
        sa.Column("snapshot_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price_low", sa.Numeric(20, 8), nullable=False),
        sa.Column("price_high", sa.Numeric(20, 8), nullable=False),
        sa.Column("side", sa.Text, nullable=False),
        sa.Column("est_volume_usd", sa.Numeric(20, 2), nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("raw_payload", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "timeframe IN ('1h','4h','1d')", name="liq_buckets_tf_check"
        ),
        sa.CheckConstraint(
            "side IN ('long_liq','short_liq')", name="liq_buckets_side_check"
        ),
        sa.CheckConstraint(
            "source IN ('A_derived','B_hyperliquid','D_coinglass')",
            name="liq_buckets_source_check",
        ),
        sa.CheckConstraint(
            "price_high > price_low", name="liq_buckets_price_order"
        ),
        sa.CheckConstraint(
            "est_volume_usd >= 0", name="liq_buckets_volume_nonneg"
        ),
    )
    op.create_index(
        "liq_buckets_symbol_tf_ts",
        "liquidation_buckets",
        ["symbol", "timeframe", sa.text("snapshot_ts DESC")],
    )
    op.create_index(
        "liq_buckets_source_ts",
        "liquidation_buckets",
        ["source", sa.text("snapshot_ts DESC")],
    )
    op.create_index(
        "liq_buckets_user_id",
        "liquidation_buckets",
        ["user_id"],
    )

    # -----------------------------------------------------------------------
    # hyperliquid_known_addresses — universe bootstrap for Provider B
    # -----------------------------------------------------------------------
    op.create_table(
        "hyperliquid_known_addresses",
        sa.Column("address", sa.Text, primary_key=True),  # 0x... 42 chars
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_account_value_usd", sa.Numeric(20, 2), nullable=True),
        sa.Column("n_positions", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "tags",
            sa.dialects.postgresql.ARRAY(sa.Text),
            nullable=False,
            server_default="{}",
        ),
        sa.CheckConstraint("address ~ '^0x[a-fA-F0-9]{40}$'", name="hl_addr_format"),
    )
    op.create_index(
        "hl_addrs_last_seen",
        "hyperliquid_known_addresses",
        [sa.text("last_seen_at DESC")],
    )
    op.create_index(
        "hl_addrs_account_value",
        "hyperliquid_known_addresses",
        [sa.text("last_account_value_usd DESC NULLS LAST")],
    )

    # -----------------------------------------------------------------------
    # liquidation_agreement_log — drives M2 weight decision
    # -----------------------------------------------------------------------
    op.create_table(
        "liquidation_agreement_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Text, nullable=False),
        sa.Column("setup_id", sa.dialects.postgresql.UUID, nullable=True),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("timeframe", sa.Text, nullable=False),
        sa.Column("proposed_zone_price", sa.Numeric(20, 8), nullable=False),
        sa.Column("proposed_zone_side", sa.Text, nullable=False),
        sa.Column("source_a_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("source_b_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("source_c_verdict", sa.Text, nullable=False),
        sa.Column("delta_a_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("delta_b_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "logged_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "timeframe IN ('1h','4h','1d')", name="agreement_log_tf_check"
        ),
        sa.CheckConstraint(
            "proposed_zone_side IN ('long_liq','short_liq')",
            name="agreement_log_side_check",
        ),
        sa.CheckConstraint(
            "source_c_verdict IN ('agree','close','disagree','skipped')",
            name="agreement_log_verdict_check",
        ),
    )
    op.create_index(
        "agreement_log_user_ts",
        "liquidation_agreement_log",
        ["user_id", sa.text("logged_at DESC")],
    )
    op.create_index(
        "agreement_log_setup",
        "liquidation_agreement_log",
        ["setup_id"],
        postgresql_where=sa.text("setup_id IS NOT NULL"),
    )

    # -----------------------------------------------------------------------
    # provider_weights — output of calibration job (computed weekly from M2)
    # -----------------------------------------------------------------------
    op.create_table(
        "liquidation_provider_weights",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("timeframe", sa.Text, nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("weight", sa.Numeric(6, 4), nullable=False),
        sa.Column("agreement_rate", sa.Numeric(6, 4), nullable=False),
        sa.Column("n_samples", sa.Integer, nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "timeframe IN ('1h','4h','1d')", name="prov_weights_tf_check"
        ),
        sa.CheckConstraint(
            "provider IN ('A_derived','B_hyperliquid','D_coinglass')",
            name="prov_weights_provider_check",
        ),
        sa.CheckConstraint(
            "weight >= 0.10 AND weight <= 1.0", name="prov_weights_floor"
        ),
    )
    op.create_index(
        "prov_weights_lookup",
        "liquidation_provider_weights",
        ["symbol", "timeframe", "provider", sa.text("computed_at DESC")],
    )


def downgrade() -> None:
    op.drop_table("liquidation_provider_weights")
    op.drop_table("liquidation_agreement_log")
    op.drop_table("hyperliquid_known_addresses")
    op.drop_table("liquidation_buckets")
