"""Pydantic models for the liquidation heatmap engine.

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
    # Post-normalize weights can dip below WEIGHT_FLOOR=0.10 (e.g. a provider
    # at floor + another at rate=1.0 yields 0.10/1.10 ≈ 0.091). The floor is
    # enforced on the RAW rate inside calibration; the stored weight is the
    # normalized value, which by construction is in [0, 1].
    weight: float = Field(ge=0.0, le=1.0)
    agreement_rate: float = Field(ge=0.0, le=1.0)
    n_samples: int = Field(ge=0)
    computed_at: datetime
