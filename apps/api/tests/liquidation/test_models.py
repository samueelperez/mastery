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
