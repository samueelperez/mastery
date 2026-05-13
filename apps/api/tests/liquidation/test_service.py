"""Unit tests for HeatmapService aggregation logic.

All DB I/O is mocked via AsyncMock on the repo. Providers are mocked via
MagicMock matching the BaseLiquidationProvider interface.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.liquidation.models import ProviderHeatmap, RawProviderBucket
from app.liquidation.service import (
    HIGH_AGREEMENT,
    MEDIUM_AGREEMENT,
    HeatmapService,
)


def _provider_mock(
    name: str,
    buckets: list[RawProviderBucket],
    as_of,
    enabled: bool = True,
    max_age_s: int = 600,
):
    p = MagicMock()
    p.name = name
    p.enabled = enabled
    p.max_age_seconds = max_age_s
    p.get_heatmap = AsyncMock(
        return_value=ProviderHeatmap(
            provider=name,
            symbol="BTCUSDT",
            timeframe="4h",
            as_of=as_of,
            buckets=buckets,
        )
    )
    return p


def _bucket(price_low: float, side: str, vol: float, provider: str, as_of) -> RawProviderBucket:
    return RawProviderBucket(
        price_low=price_low,
        price_high=price_low + 100,
        side=side,
        est_volume_usd=vol,
        provider=provider,  # type: ignore[arg-type]
        as_of=as_of,
    )


def _repo_mock():
    repo = AsyncMock()
    repo.fetch_weights = AsyncMock(return_value={})
    repo.persist_snapshot = AsyncMock()
    return repo


class TestServiceMerge:
    async def test_no_providers_returns_empty_warning(self) -> None:
        s = HeatmapService(providers=[], repo=_repo_mock())
        snap = await s.get_snapshot("BTCUSDT", "4h", 84_500.0)
        assert snap.magnet_zones == []
        assert "no_active_providers" in snap.provenance.warnings

    async def test_one_provider_warns_degraded(self, now_utc) -> None:
        p = _provider_mock(
            "A_derived",
            [_bucket(84_000, "short_liq", 1_500_000, "A_derived", now_utc)],
            as_of=now_utc,
        )
        s = HeatmapService(providers=[p], repo=_repo_mock())
        snap = await s.get_snapshot("BTCUSDT", "4h", 84_500.0)
        assert "degraded_few_sources" in snap.provenance.warnings
        assert snap.sources_agreement == 1.0

    async def test_two_providers_merge_buckets(self, now_utc) -> None:
        p1 = _provider_mock(
            "A_derived",
            [
                _bucket(84_000, "short_liq", 800_000, "A_derived", now_utc),
                _bucket(82_500, "long_liq", 1_200_000, "A_derived", now_utc),
            ],
            as_of=now_utc,
        )
        p2 = _provider_mock(
            "B_hyperliquid",
            [
                _bucket(84_000, "short_liq", 700_000, "B_hyperliquid", now_utc),
                _bucket(82_500, "long_liq", 900_000, "B_hyperliquid", now_utc),
            ],
            as_of=now_utc,
        )
        s = HeatmapService(providers=[p1, p2], repo=_repo_mock())
        snap = await s.get_snapshot("BTCUSDT", "4h", 84_500.0)
        assert len(snap.magnet_zones) >= 2
        assert set(snap.sources_used) == {"A_derived", "B_hyperliquid"}

    async def test_stale_provider_excluded(self) -> None:
        # Use live time so the staleness check (which compares to live now())
        # is well-defined regardless of when the test runs.
        from datetime import UTC, datetime

        live_now = datetime.now(tz=UTC)
        stale_ts = live_now - timedelta(hours=1)  # past any reasonable max_age
        p_stale = _provider_mock(
            "A_derived",
            [_bucket(84_000, "short_liq", 1_500_000, "A_derived", stale_ts)],
            as_of=stale_ts,
            max_age_s=30,
        )
        p_fresh = _provider_mock(
            "B_hyperliquid",
            [_bucket(84_000, "short_liq", 1_500_000, "B_hyperliquid", live_now)],
            as_of=live_now,
        )
        s = HeatmapService(providers=[p_stale, p_fresh], repo=_repo_mock())
        snap = await s.get_snapshot("BTCUSDT", "4h", 84_500.0)
        assert "provider_stale:A_derived" in snap.provenance.warnings
        assert "A_derived" not in snap.sources_used

    async def test_nearest_zones_directional(self, now_utc) -> None:
        p1 = _provider_mock(
            "A_derived",
            [
                _bucket(84_700, "short_liq", 1_000_000, "A_derived", now_utc),  # ABOVE
                _bucket(85_300, "short_liq", 500_000, "A_derived", now_utc),  # ABOVE, farther
                _bucket(83_900, "long_liq", 900_000, "A_derived", now_utc),  # BELOW
            ],
            as_of=now_utc,
        )
        p2 = _provider_mock(
            "B_hyperliquid",
            [
                _bucket(84_700, "short_liq", 800_000, "B_hyperliquid", now_utc),
                _bucket(83_900, "long_liq", 1_100_000, "B_hyperliquid", now_utc),
            ],
            as_of=now_utc,
        )
        s = HeatmapService(providers=[p1, p2], repo=_repo_mock())
        snap = await s.get_snapshot("BTCUSDT", "4h", 84_500.0)

        assert snap.nearest_short_liq is not None
        assert snap.nearest_short_liq.distance_pct > 0  # above
        assert snap.nearest_long_liq is not None
        assert snap.nearest_long_liq.distance_pct < 0  # below

    async def test_imbalance_long_heavy(self, now_utc) -> None:
        p = _provider_mock(
            "A_derived",
            [
                _bucket(83_900, "long_liq", 2_000_000, "A_derived", now_utc),
                _bucket(83_800, "long_liq", 1_000_000, "A_derived", now_utc),
                _bucket(84_700, "short_liq", 500_000, "A_derived", now_utc),
            ],
            as_of=now_utc,
        )
        p2 = _provider_mock(
            "B_hyperliquid",
            [
                _bucket(83_900, "long_liq", 1_500_000, "B_hyperliquid", now_utc),
                _bucket(84_700, "short_liq", 400_000, "B_hyperliquid", now_utc),
            ],
            as_of=now_utc,
        )
        s = HeatmapService(providers=[p, p2], repo=_repo_mock())
        snap = await s.get_snapshot("BTCUSDT", "4h", 84_500.0)
        assert snap.imbalance_ratio > 1.5

    async def test_density_high_when_zones_concentrated(self, now_utc) -> None:
        # All zones within ±2% of 84_500 → density should be high.
        p = _provider_mock(
            "A_derived",
            [
                _bucket(84_300, "long_liq", 1_000_000, "A_derived", now_utc),
                _bucket(84_700, "short_liq", 1_000_000, "A_derived", now_utc),
            ],
            as_of=now_utc,
        )
        p2 = _provider_mock(
            "B_hyperliquid",
            [
                _bucket(84_300, "long_liq", 800_000, "B_hyperliquid", now_utc),
                _bucket(84_700, "short_liq", 800_000, "B_hyperliquid", now_utc),
            ],
            as_of=now_utc,
        )
        s = HeatmapService(providers=[p, p2], repo=_repo_mock())
        snap = await s.get_snapshot("BTCUSDT", "4h", 84_500.0)
        assert snap.cluster_density > 0.8

    async def test_uniform_weights_when_no_calibration(self, now_utc) -> None:
        repo = _repo_mock()
        # Empty weights → uniform.
        p1 = _provider_mock(
            "A_derived",
            [_bucket(84_000, "short_liq", 1_000_000, "A_derived", now_utc)],
            as_of=now_utc,
        )
        p2 = _provider_mock(
            "B_hyperliquid",
            [_bucket(84_000, "short_liq", 1_000_000, "B_hyperliquid", now_utc)],
            as_of=now_utc,
        )
        s = HeatmapService(providers=[p1, p2], repo=repo)
        snap = await s.get_snapshot("BTCUSDT", "4h", 84_500.0)
        zone = snap.magnet_zones[0]
        assert zone.source_breakdown["A_derived"] == pytest.approx(
            zone.source_breakdown["B_hyperliquid"], rel=0.01
        )

    async def test_stored_weights_applied(self, now_utc) -> None:
        repo = AsyncMock()
        repo.fetch_weights = AsyncMock(
            return_value={
                "A_derived": 0.70,
                "B_hyperliquid": 0.30,
            }
        )
        repo.persist_snapshot = AsyncMock()

        p1 = _provider_mock(
            "A_derived",
            [_bucket(84_000, "short_liq", 1_000_000, "A_derived", now_utc)],
            as_of=now_utc,
        )
        p2 = _provider_mock(
            "B_hyperliquid",
            [_bucket(84_000, "short_liq", 1_000_000, "B_hyperliquid", now_utc)],
            as_of=now_utc,
        )
        s = HeatmapService(providers=[p1, p2], repo=repo)
        snap = await s.get_snapshot("BTCUSDT", "4h", 84_500.0)
        zone = snap.magnet_zones[0]
        ratio = zone.source_breakdown["A_derived"] / zone.source_breakdown["B_hyperliquid"]
        assert 2.0 < ratio < 2.6  # ~2.33 = 0.70/0.30

    async def test_provider_exception_marked_failed(self, now_utc) -> None:
        p_ok = _provider_mock(
            "A_derived",
            [_bucket(84_000, "short_liq", 1_000_000, "A_derived", now_utc)],
            as_of=now_utc,
        )
        p_bad = MagicMock()
        p_bad.name = "B_hyperliquid"
        p_bad.enabled = True
        p_bad.max_age_seconds = 600
        p_bad.get_heatmap = AsyncMock(side_effect=RuntimeError("oops"))

        s = HeatmapService(providers=[p_ok, p_bad], repo=_repo_mock())
        snap = await s.get_snapshot("BTCUSDT", "4h", 84_500.0)
        assert "provider_failed:B_hyperliquid" in snap.provenance.warnings
        assert "A_derived" in snap.sources_used
        assert "B_hyperliquid" not in snap.sources_used

    async def test_persistence_called_with_raw_buckets(self, now_utc) -> None:
        repo = _repo_mock()
        buckets = [_bucket(84_000, "short_liq", 1_500_000, "A_derived", now_utc)]
        p = _provider_mock("A_derived", buckets, as_of=now_utc)

        s = HeatmapService(providers=[p], repo=repo)
        await s.get_snapshot("BTCUSDT", "4h", 84_500.0)

        # repo.persist_snapshot was called with the raw_by_source dict.
        repo.persist_snapshot.assert_awaited_once()
        args = repo.persist_snapshot.await_args.args
        _, raw_by_source = args
        assert "A_derived" in raw_by_source
        assert len(raw_by_source["A_derived"]) == 1


class TestServiceConfidence:
    async def test_high_confidence_when_agreement_high(self, now_utc) -> None:
        # Two providers pointing at near-identical zones → high agreement.
        p1 = _provider_mock(
            "A_derived",
            [
                _bucket(84_000, "short_liq", 1_000_000, "A_derived", now_utc),
                _bucket(82_500, "long_liq", 800_000, "A_derived", now_utc),
            ],
            as_of=now_utc,
        )
        p2 = _provider_mock(
            "B_hyperliquid",
            [
                _bucket(84_000, "short_liq", 950_000, "B_hyperliquid", now_utc),
                _bucket(82_500, "long_liq", 850_000, "B_hyperliquid", now_utc),
            ],
            as_of=now_utc,
        )
        s = HeatmapService(providers=[p1, p2], repo=_repo_mock())
        snap = await s.get_snapshot("BTCUSDT", "4h", 84_500.0)
        assert snap.sources_agreement >= HIGH_AGREEMENT
        assert all(z.confidence == "high" for z in snap.magnet_zones)

    async def test_low_confidence_when_disagree(self, now_utc) -> None:
        # Providers point at totally different zones → low agreement.
        p1 = _provider_mock(
            "A_derived",
            [_bucket(84_000, "short_liq", 1_000_000, "A_derived", now_utc)],
            as_of=now_utc,
        )
        p2 = _provider_mock(
            "B_hyperliquid",
            [_bucket(80_000, "long_liq", 1_000_000, "B_hyperliquid", now_utc)],
            as_of=now_utc,
        )
        s = HeatmapService(providers=[p1, p2], repo=_repo_mock())
        snap = await s.get_snapshot("BTCUSDT", "4h", 84_500.0)
        assert snap.sources_agreement < MEDIUM_AGREEMENT
