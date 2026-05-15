# 01 — Models and Schema

<context>
This spec defines the Pydantic models and the SQL migration that the rest of the module depends on. Build these before any provider or service code. Once merged, providers and service can be developed in parallel.
</context>

<deliverables>
- `apps/api/app/liquidation/models.py` — Pydantic models.
- `apps/api/alembic/versions/025_liquidation_engine.py` — Alembic migration.
- `apps/api/tests/liquidation/test_models.py` — unit tests for model validation.
</deliverables>

<file_apps_api_app_liquidation_models_py>

```python
"""
Pydantic models for the liquidation heatmap engine.

All public types exposed by the module live here. Providers, service, and
tools all consume these.

Conventions:
- Datetimes are TZ-aware UTC.
- All numeric values that represent USD nocional are floats (no Decimal here
  — Decimal lives in `paper_trading/` and order execution).
- Side enum uses 'long_liq' / 'short_liq' to disambiguate from trade sides
  (which use 'long' / 'short' / 'buy' / 'sell' depending on context).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Re-use the standard Provenance envelope from the agent tools.
from app.agent.tools._envelope import Provenance

# ---------------------------------------------------------------------------
# Aliases for readability
# ---------------------------------------------------------------------------
TimeframeLiteral = Literal["1h", "4h", "1d"]
SideLiteral = Literal["long_liq", "short_liq"]
ConfidenceLiteral = Literal["low", "medium", "high"]
ProviderName = Literal["A_derived", "B_hyperliquid", "D_coinglass"]
TDVerdict = Literal["agree", "close", "disagree", "skipped"]


# ---------------------------------------------------------------------------
# Core types
# ---------------------------------------------------------------------------
class MagnetZone(BaseModel):
    """A single price band where leveraged positions cluster their liquidation
    prices. Either long positions liquidating (price falling) or short
    positions liquidating (price rising)."""

    model_config = ConfigDict(frozen=True)

    price_low: float = Field(gt=0, description="Lower price bound of the bucket")
    price_high: float = Field(gt=0, description="Upper price bound of the bucket")
    side: SideLiteral = Field(description="Which side gets liquidated in this zone")
    est_volume_usd: float = Field(
        ge=0,
        description="Estimated notional USD that would be liquidated if price reaches this band",
    )
    distance_pct: float = Field(
        description="Signed distance to current price as percentage. Negative for zones below current, positive above.",
    )
    source_breakdown: dict[ProviderName, float] = Field(
        default_factory=dict,
        description="Per-source contribution to est_volume_usd. Sum may not equal total if weights are not 1.0.",
    )
    confidence: ConfidenceLiteral = Field(
        description="Confidence in this zone, derived from agreement across sources.",
    )

    @field_validator("price_high")
    @classmethod
    def _high_greater_than_low(cls, v: float, info) -> float:
        low = info.data.get("price_low")
        if low is not None and v <= low:
            raise ValueError(f"price_high ({v}) must be > price_low ({low})")
        return v


class HeatmapSnapshot(BaseModel):
    """A complete snapshot of liquidation magnet zones for one symbol at one
    timeframe, at a single point in time."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(description="Internal symbol, e.g. 'BTCUSDT'")
    timeframe: TimeframeLiteral
    current_price: float = Field(gt=0)
    as_of: datetime = Field(description="Timestamp of the snapshot (TZ-aware UTC)")

    magnet_zones: list[MagnetZone] = Field(
        default_factory=list,
        description="Zones within max_distance_pct, sorted by abs(distance_pct) ascending.",
    )

    # Convenience accessors — populated by the service, not the providers.
    nearest_long_liq: MagnetZone | None = Field(
        default=None,
        description="Closest long_liq zone (price below current). None if no significant zone within range.",
    )
    nearest_short_liq: MagnetZone | None = Field(
        default=None,
        description="Closest short_liq zone (price above current). None if no significant zone within range.",
    )

    imbalance_ratio: float = Field(
        ge=0,
        description="long_vol / short_vol within ±5% of current price. 1.0 = balanced.",
    )
    cluster_density: float = Field(
        ge=0,
        le=1,
        description="Concentration of liquidation volume within ±2% of current price, normalized 0-1.",
    )

    sources_used: list[ProviderName] = Field(
        description="Providers that contributed data to this snapshot."
    )
    sources_agreement: float = Field(
        ge=0,
        le=1,
        description="1 - coefficient of variation across providers for top 5 buckets. Higher = more agreement.",
    )

    provenance: Provenance

    @field_validator("as_of")
    @classmethod
    def _must_be_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("as_of must be TZ-aware UTC")
        return v


# ---------------------------------------------------------------------------
# Provider-internal types (not exposed to the agent directly)
# ---------------------------------------------------------------------------
class RawProviderBucket(BaseModel):
    """What a provider returns before aggregation. The service merges these
    across providers into MagnetZones."""

    model_config = ConfigDict(frozen=True)

    price_low: float = Field(gt=0)
    price_high: float = Field(gt=0)
    side: SideLiteral
    est_volume_usd: float = Field(ge=0)
    provider: ProviderName
    as_of: datetime

    @field_validator("as_of")
    @classmethod
    def _must_be_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("as_of must be TZ-aware UTC")
        return v


class ProviderHeatmap(BaseModel):
    """A provider's contribution: raw buckets plus metadata."""

    model_config = ConfigDict(frozen=True)

    provider: ProviderName
    symbol: str
    timeframe: TimeframeLiteral
    as_of: datetime
    buckets: list[RawProviderBucket]
    warnings: list[str] = Field(default_factory=list)

    @field_validator("as_of")
    @classmethod
    def _must_be_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("as_of must be TZ-aware UTC")
        return v


# ---------------------------------------------------------------------------
# Calibration types (drives M2 weight decision)
# ---------------------------------------------------------------------------
class AgreementLogEntry(BaseModel):
    """One row of the `liquidation_agreement_log` table. Used by calibration
    to recompute provider weights."""

    symbol: str
    timeframe: TimeframeLiteral
    proposed_zone_price: float
    proposed_zone_side: SideLiteral
    source_a_price: float | None
    source_b_price: float | None
    source_c_verdict: TDVerdict
    delta_a_pct: float | None
    delta_b_pct: float | None
    logged_at: datetime


class ProviderWeight(BaseModel):
    """Computed weight for a provider in a (symbol, timeframe) cell."""

    symbol: str
    timeframe: TimeframeLiteral
    provider: ProviderName
    weight: float = Field(ge=0.10, le=1.0)
    agreement_rate: float = Field(ge=0.0, le=1.0)
    n_samples: int = Field(ge=0)
    computed_at: datetime
```
</file_apps_api_app_liquidation_models_py>

<file_alembic_025>

```python
"""Liquidation heatmap engine — buckets, addresses, agreement log

Revision ID: 025_liquidation_engine
Revises: 024_paper_trading_engine
Create Date: 2026-05-12 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "025_liquidation_engine"
down_revision: str | None = "024_paper_trading_engine"
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
        sa.CheckConstraint("price_high > price_low", name="liq_buckets_price_order"),
        sa.CheckConstraint("est_volume_usd >= 0", name="liq_buckets_volume_nonneg"),
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
        sa.CheckConstraint("weight >= 0.10 AND weight <= 1.0", name="prov_weights_floor"),
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
```

</file_alembic_025>

<file_tests_liquidation_test_models_py>

```python
"""Unit tests for liquidation models. Pure validation; no DB."""
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.agent.tools._envelope import Provenance
from app.liquidation.models import (
    HeatmapSnapshot,
    MagnetZone,
    ProviderHeatmap,
    RawProviderBucket,
)


def _provenance() -> Provenance:
    return Provenance(
        source="liquidation_heatmap_engine",
        as_of=datetime.now(tz=UTC),
        rows=0,
        warnings=[],
    )


class TestMagnetZone:
    def test_valid(self) -> None:
        z = MagnetZone(
            price_low=84_000,
            price_high=84_200,
            side="short_liq",
            est_volume_usd=1_500_000,
            distance_pct=0.5,
            confidence="high",
        )
        assert z.price_high > z.price_low

    def test_price_high_must_exceed_low(self) -> None:
        with pytest.raises(ValidationError, match="price_high"):
            MagnetZone(
                price_low=84_200,
                price_high=84_000,  # inverted
                side="short_liq",
                est_volume_usd=1_500_000,
                distance_pct=0.5,
                confidence="high",
            )

    def test_negative_volume_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MagnetZone(
                price_low=84_000,
                price_high=84_200,
                side="short_liq",
                est_volume_usd=-1,
                distance_pct=0.5,
                confidence="high",
            )

    def test_frozen(self) -> None:
        z = MagnetZone(
            price_low=84_000,
            price_high=84_200,
            side="short_liq",
            est_volume_usd=1_500_000,
            distance_pct=0.5,
            confidence="high",
        )
        with pytest.raises(ValidationError):
            z.price_low = 0  # type: ignore[misc]


class TestHeatmapSnapshot:
    def test_valid_empty(self) -> None:
        s = HeatmapSnapshot(
            symbol="BTCUSDT",
            timeframe="4h",
            current_price=84_500,
            as_of=datetime.now(tz=UTC),
            magnet_zones=[],
            imbalance_ratio=1.0,
            cluster_density=0.0,
            sources_used=["A_derived"],
            sources_agreement=1.0,
            provenance=_provenance(),
        )
        assert s.nearest_long_liq is None
        assert s.nearest_short_liq is None

    def test_naive_datetime_rejected(self) -> None:
        with pytest.raises(ValidationError, match="TZ-aware"):
            HeatmapSnapshot(
                symbol="BTCUSDT",
                timeframe="4h",
                current_price=84_500,
                as_of=datetime(2026, 5, 12, 12, 0, 0),  # naive
                magnet_zones=[],
                imbalance_ratio=1.0,
                cluster_density=0.0,
                sources_used=["A_derived"],
                sources_agreement=1.0,
                provenance=_provenance(),
            )

    def test_cluster_density_bounded(self) -> None:
        with pytest.raises(ValidationError):
            HeatmapSnapshot(
                symbol="BTCUSDT",
                timeframe="4h",
                current_price=84_500,
                as_of=datetime.now(tz=UTC),
                magnet_zones=[],
                imbalance_ratio=1.0,
                cluster_density=1.5,  # > 1
                sources_used=["A_derived"],
                sources_agreement=1.0,
                provenance=_provenance(),
            )


class TestRawProviderBucket:
    def test_valid(self) -> None:
        b = RawProviderBucket(
            price_low=84_000,
            price_high=84_200,
            side="short_liq",
            est_volume_usd=1_500_000,
            provider="A_derived",
            as_of=datetime.now(tz=UTC),
        )
        assert b.provider == "A_derived"

    def test_invalid_provider(self) -> None:
        with pytest.raises(ValidationError):
            RawProviderBucket(
                price_low=84_000,
                price_high=84_200,
                side="short_liq",
                est_volume_usd=1_500_000,
                provider="E_unknown",  # type: ignore[arg-type]
                as_of=datetime.now(tz=UTC),
            )


class TestProviderHeatmap:
    def test_empty_warnings_default(self) -> None:
        h = ProviderHeatmap(
            provider="A_derived",
            symbol="BTCUSDT",
            timeframe="4h",
            as_of=datetime.now(tz=UTC),
            buckets=[],
        )
        assert h.warnings == []
```

</file_tests_liquidation_test_models_py>

<gotchas>
- `Provenance` is reused from `agent/tools/_envelope.py`. Don't duplicate. If the import fails, that file is missing — fix that import path first, don't redefine.
- The migration depends on `024_paper_trading_engine` existing. If the current `down_revision` doesn't match what's in your repo, regenerate it with `alembic heads`.
- `JSONB` and `UUID` types are imported via `sa.dialects.postgresql`. Don't try `sa.JSONB` — doesn't exist.
- `server_default="{}"` for arrays needs the column type to be set BEFORE the default; SQLAlchemy generates `ARRAY(TEXT)[]` syntax correctly only with the type pre-declared.
- Address format check uses Postgres `~` regex operator. Test it manually before relying.
- Don't add `down_revision` foreign keys to the previous migration's tables — Alembic resolves them at runtime by revision graph.
</gotchas>

<acceptance>
- [ ] `models.py` imports cleanly with `python -c "from app.liquidation.models import HeatmapSnapshot"`.
- [ ] `alembic upgrade head` succeeds on a fresh DB.
- [ ] `alembic downgrade -1` cleanly drops the 4 new tables.
- [ ] `pytest apps/api/tests/liquidation/test_models.py -v` passes 100%.
- [ ] No new dependencies added to `pyproject.toml` (everything uses what's already there).
</acceptance>
