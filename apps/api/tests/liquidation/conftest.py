"""Shared fixtures for liquidation tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.agent.tools._envelope import Provenance
from app.liquidation.models import (
    MagnetZone,
    ProviderHeatmap,
    RawProviderBucket,
)


@pytest.fixture
def now_utc() -> datetime:
    return datetime.now(tz=UTC)


@pytest.fixture
def fake_provenance(now_utc: datetime) -> Provenance:
    return Provenance(
        source="liquidation_heatmap_engine",
        as_of=now_utc,
        rows=0,
        warnings=[],
    )


@pytest.fixture
def sample_magnet_zone() -> MagnetZone:
    return MagnetZone(
        price_low=84_000,
        price_high=84_200,
        side="short_liq",
        est_volume_usd=1_500_000,
        distance_pct=0.5,
        source_breakdown={"A_derived": 800_000, "B_hyperliquid": 700_000},
        confidence="high",
    )


@pytest.fixture
def sample_provider_heatmap(now_utc: datetime) -> ProviderHeatmap:
    return ProviderHeatmap(
        provider="A_derived",
        symbol="BTCUSDT",
        timeframe="4h",
        as_of=now_utc,
        buckets=[
            RawProviderBucket(
                price_low=84_000,
                price_high=84_200,
                side="short_liq",
                est_volume_usd=1_500_000,
                provider="A_derived",
                as_of=now_utc,
            ),
            RawProviderBucket(
                price_low=82_500,
                price_high=82_700,
                side="long_liq",
                est_volume_usd=2_100_000,
                provider="A_derived",
                as_of=now_utc,
            ),
        ],
    )
