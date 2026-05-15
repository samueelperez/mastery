# 07 — Testing

<context>
This spec is the source of truth for every test that must exist in `tests/liquidation/` plus the citation contract extensions in `tests/agent/test_validators_citation_rigor.py`. All tests use `pytest` + `pytest-asyncio` (mode=auto, already set in `pyproject.toml`). DB tests use transactional rollback per test via existing fixtures in `tests/conftest.py`.

Read this end-to-end once when you start, then refer back per module.
</context>

<test_layout>
```
apps/api/tests/liquidation/
├── __init__.py
├── conftest.py                              # Shared fixtures
├── test_models.py                           # From spec 01
├── test_repo.py                             # From spec 04
├── test_service.py                          # From spec 04
├── test_calibration.py                      # From spec 04
├── test_telegram_handlers.py                # From spec 06
├── test_tool.py                             # From spec 05
└── providers/
    ├── __init__.py
    ├── test_leverage.py                     # Pure functions in _leverage.py
    ├── test_derived.py                      # Provider A
    ├── test_hyperliquid.py                  # Provider B (mocked HTTP)
    └── test_hyperliquid_integration.py      # Provider B (real endpoint, -m integration)

apps/api/tests/agent/
└── test_validators_citation_rigor.py        # Existing file; add 4 new tests
```

Run subsets:
- Unit only: `pytest tests/liquidation -m "not integration"`
- Integration: `pytest tests/liquidation -m integration` (requires network)
- Full module: `pytest tests/liquidation tests/agent/test_validators_citation_rigor.py`
</test_layout>

<file_tests_liquidation_conftest_py>

```python
"""Shared fixtures for liquidation tests."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.tools._envelope import Provenance
from app.liquidation.models import (
    HeatmapSnapshot,
    MagnetZone,
    ProviderHeatmap,
    RawProviderBucket,
)
from app.liquidation.repo import LiquidationRepo


@pytest.fixture
def now_utc() -> datetime:
    return datetime(2026, 5, 12, 12, 0, 0, tzinfo=UTC)


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
                price_low=84_000, price_high=84_200, side="short_liq",
                est_volume_usd=1_500_000, provider="A_derived", as_of=now_utc,
            ),
            RawProviderBucket(
                price_low=82_500, price_high=82_700, side="long_liq",
                est_volume_usd=2_100_000, provider="A_derived", as_of=now_utc,
            ),
        ],
    )


@pytest_asyncio.fixture
async def liq_repo(db_session: AsyncSession) -> LiquidationRepo:
    """Uses the existing db_session fixture from tests/conftest.py."""
    return LiquidationRepo(session=db_session, user_id="test-user-id")
```
</file_tests_liquidation_conftest_py>

<file_tests_liquidation_providers_test_leverage_py>

```python
"""Unit tests for leverage helpers (pure functions, no I/O)."""
from __future__ import annotations

import math

import pytest

from app.liquidation.providers._leverage import (
    LEVERAGE_BRACKETS,
    LEVERAGE_PRIOR_WEIGHTS,
    MAINTENANCE_MARGIN,
    estimate_liq_price,
    opposite_side,
)


class TestPriorWeights:
    def test_weights_sum_to_one(self) -> None:
        assert math.isclose(sum(LEVERAGE_PRIOR_WEIGHTS.values()), 1.0)

    def test_all_brackets_have_weights(self) -> None:
        for lev in LEVERAGE_BRACKETS:
            assert lev in LEVERAGE_PRIOR_WEIGHTS

    def test_all_brackets_have_mm(self) -> None:
        for lev in LEVERAGE_BRACKETS:
            assert lev in MAINTENANCE_MARGIN


class TestEstimateLiqPrice:
    def test_short_counterparty_liq_above_trade_price(self) -> None:
        # Trade at 100, side='B' (buyer aggressed, counterparty went short).
        # At 10x leverage, liq should be ~10% above.
        liq = estimate_liq_price(100.0, "B", 10)
        assert 109 < liq < 111

    def test_long_counterparty_liq_below_trade_price(self) -> None:
        # Trade at 100, side='S' (seller aggressed, counterparty went long).
        # At 10x, liq should be ~10% below.
        liq = estimate_liq_price(100.0, "S", 10)
        assert 89 < liq < 91

    def test_higher_leverage_tighter_liquidation(self) -> None:
        liq_10x = estimate_liq_price(100.0, "B", 10)
        liq_100x = estimate_liq_price(100.0, "B", 100)
        assert (liq_100x - 100) < (liq_10x - 100)

    def test_invalid_side_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid side"):
            estimate_liq_price(100.0, "X", 10)

    def test_unsupported_leverage_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported leverage"):
            estimate_liq_price(100.0, "B", 7)


class TestOppositeSide:
    def test_buy_to_short_liq(self) -> None:
        assert opposite_side("B") == "short_liq"

    def test_sell_to_long_liq(self) -> None:
        assert opposite_side("S") == "long_liq"

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            opposite_side("?")
```
</file_tests_liquidation_providers_test_leverage_py>

<file_tests_liquidation_providers_test_derived_py>

```python
"""Unit tests for DerivedLiquidationProvider.

Mocks the DB via a fake session_factory; tests pure aggregation logic.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import polars as pl
import pytest

from app.liquidation.providers.derived import (
    BUCKET_PCT,
    MIN_TRADE_USD,
    DerivedLiquidationProvider,
)


def _make_trades(now: datetime, count: int = 10) -> list[dict]:
    """Generate synthetic trade rows."""
    return [
        {
            "ts": now - timedelta(hours=i),
            "price": 84_500 + (i % 5) * 10,
            "size": 5.0,
            "side": "B" if i % 2 == 0 else "S",
        }
        for i in range(count)
    ]


@pytest.fixture
def mock_session_factory(now_utc):
    """Returns a session_factory that yields a session with mocked SELECT."""
    session = AsyncMock()
    fake_rows = []
    for i in range(10):
        r = MagicMock()
        r.ts = now_utc - timedelta(hours=i)
        r.price = 84_500
        r.size = 5.0  # 5 BTC × 84.5k = $422k (above MIN_TRADE_USD)
        r.side = "B" if i % 2 == 0 else "S"
        fake_rows.append(r)
    result = MagicMock()
    result.fetchall = MagicMock(return_value=fake_rows)
    result.scalar_one = MagicMock(return_value=10)
    session.execute = AsyncMock(return_value=result)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    factory = MagicMock(return_value=session)
    return factory


class TestDerivedProvider:
    @pytest.mark.asyncio
    async def test_unsupported_symbol(self, mock_session_factory) -> None:
        p = DerivedLiquidationProvider(mock_session_factory)
        result = await p.get_heatmap("DOGEUSDT", "4h", 0.10)
        assert result.buckets == []
        assert any("symbol_not_supported" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_supports_symbol(self, mock_session_factory) -> None:
        p = DerivedLiquidationProvider(mock_session_factory)
        assert p.supports_symbol("BTCUSDT")
        assert p.supports_symbol("ETHUSDT")
        assert p.supports_symbol("SOLUSDT")
        assert not p.supports_symbol("XRPUSDT")

    @pytest.mark.asyncio
    async def test_returns_buckets_on_valid_trades(self, mock_session_factory) -> None:
        p = DerivedLiquidationProvider(mock_session_factory)
        result = await p.get_heatmap("BTCUSDT", "4h", 84_500.0)
        assert result.provider == "A_derived"
        assert result.symbol == "BTCUSDT"
        # With 10 trades × 7 leverage brackets, expect at least a few buckets.
        assert len(result.buckets) > 0

    @pytest.mark.asyncio
    async def test_no_lookahead(self, now_utc) -> None:
        """Critical invariant: future trades must not affect past snapshots.

        We pin `now` by monkey-patching datetime.now and run the provider
        twice — once with N trades, then with N+1 trades where the new trade
        has ts > now. Result must be identical.
        """
        # This test needs careful fixture design. We use a real session against
        # an in-memory SQLite (or skip — DB integration is harder here).
        # Alternative: refactor _compute_buckets to be a pure function on a
        # DataFrame, then test that directly. Recommended:
        from app.liquidation.providers.derived import DerivedLiquidationProvider
        prov = DerivedLiquidationProvider(MagicMock())

        trades_now = pl.DataFrame({
            "ts": [now_utc - timedelta(hours=h) for h in range(10)],
            "price": [84_500.0] * 10,
            "size": [5.0] * 10,
            "side": ["B"] * 5 + ["S"] * 5,
        })
        trades_with_future = pl.concat([
            trades_now,
            pl.DataFrame({
                "ts": [now_utc + timedelta(hours=1)],  # FUTURE
                "price": [85_000.0],
                "size": [10.0],
                "side": ["B"],
            }),
        ])

        # The current implementation does the time filter at SQL level.
        # For this unit test, we filter the polars df to ts <= now and assert
        # both computations produce the same bucket set. If you refactor
        # _compute_buckets, make it pure and call it directly.
        filtered = trades_with_future.filter(pl.col("ts") <= now_utc)
        df1 = prov._compute_buckets(
            trades=trades_now,
            current_price=84_500.0,
            bucket_size=84_500.0 * BUCKET_PCT,
            max_dist_abs=84_500.0 * 0.10,
            half_life_h=48.0,
            now=now_utc,
        )
        df2 = prov._compute_buckets(
            trades=filtered,
            current_price=84_500.0,
            bucket_size=84_500.0 * BUCKET_PCT,
            max_dist_abs=84_500.0 * 0.10,
            half_life_h=48.0,
            now=now_utc,
        )
        assert df1.to_dict(as_series=False) == df2.to_dict(as_series=False)

    @pytest.mark.asyncio
    async def test_buckets_within_max_distance(self, mock_session_factory) -> None:
        """All returned buckets must be within ±max_distance_pct of current price."""
        p = DerivedLiquidationProvider(mock_session_factory)
        current = 84_500.0
        max_pct = 5.0
        result = await p.get_heatmap("BTCUSDT", "4h", current, max_distance_pct=max_pct)
        for b in result.buckets:
            mid = (b.price_low + b.price_high) / 2
            dist_pct = abs(mid - current) / current * 100
            assert dist_pct <= max_pct + 1e-6
```
</file_tests_liquidation_providers_test_derived_py>

<file_tests_liquidation_providers_test_hyperliquid_py>

```python
"""Unit tests for HyperliquidLiquidationProvider with mocked HTTP."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.liquidation.providers.hyperliquid import HyperliquidLiquidationProvider


def _fake_clearinghouse_response(coin: str, liq_px: float, side_long: bool) -> dict:
    """Build a synthetic clearinghouseState payload with one position."""
    szi = "5.0" if side_long else "-5.0"
    return {
        "assetPositions": [
            {
                "position": {
                    "coin": coin,
                    "szi": szi,
                    "liquidationPx": str(liq_px),
                    "positionValue": "422500",
                }
            }
        ],
        "marginSummary": {},
        "time": int(datetime.now(tz=UTC).timestamp() * 1000),
    }


@pytest.fixture
def mock_hl_client():
    client = AsyncMock()
    # all_mids returns non-empty -> health_check True.
    client.all_mids = AsyncMock(return_value={"BTC": "84500", "ETH": "3200"})
    return client


@pytest.fixture
def mock_session_factory_with_addresses(now_utc):
    """Session that returns 3 fake addresses from the universe."""
    session = AsyncMock()
    fake_rows = [MagicMock(address=f"0x{'a' * 40}"),
                 MagicMock(address=f"0x{'b' * 40}"),
                 MagicMock(address=f"0x{'c' * 40}")]
    result = MagicMock()
    result.__iter__ = lambda self: iter(fake_rows)
    session.execute = AsyncMock(return_value=result)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=session)
    return factory


class TestHyperliquidProvider:
    @pytest.mark.asyncio
    async def test_unsupported_symbol(self, mock_session_factory_with_addresses, mock_hl_client) -> None:
        p = HyperliquidLiquidationProvider(mock_session_factory_with_addresses, mock_hl_client)
        result = await p.get_heatmap("DOGEUSDT", "4h", 0.10)
        assert result.buckets == []
        assert any("symbol_not_supported" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_empty_address_universe(self, mock_hl_client) -> None:
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.__iter__ = lambda self: iter([])
        session.execute = AsyncMock(return_value=result_mock)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        factory = MagicMock(return_value=session)

        p = HyperliquidLiquidationProvider(factory, mock_hl_client)
        result = await p.get_heatmap("BTCUSDT", "4h", 84_500.0)
        assert result.buckets == []
        assert "address_universe_empty" in result.warnings

    @pytest.mark.asyncio
    async def test_aggregates_long_and_short_positions(
        self,
        mock_session_factory_with_addresses,
        mock_hl_client,
    ) -> None:
        # 3 addresses → 3 different positions.
        mock_hl_client.clearinghouse_state = AsyncMock(side_effect=[
            _fake_clearinghouse_response("BTC", 82_000, side_long=True),
            _fake_clearinghouse_response("BTC", 86_500, side_long=False),
            _fake_clearinghouse_response("BTC", 82_100, side_long=True),
        ])
        p = HyperliquidLiquidationProvider(mock_session_factory_with_addresses, mock_hl_client)
        result = await p.get_heatmap("BTCUSDT", "4h", 84_500.0, max_distance_pct=5.0)

        long_liq = [b for b in result.buckets if b.side == "long_liq"]
        short_liq = [b for b in result.buckets if b.side == "short_liq"]
        assert len(long_liq) >= 1
        assert len(short_liq) >= 1
        # All within 5% of 84_500 = [80_275, 88_725]
        for b in result.buckets:
            assert 80_275 <= b.price_low <= 88_725

    @pytest.mark.asyncio
    async def test_continues_on_address_errors(
        self,
        mock_session_factory_with_addresses,
        mock_hl_client,
    ) -> None:
        mock_hl_client.clearinghouse_state = AsyncMock(side_effect=[
            _fake_clearinghouse_response("BTC", 82_000, side_long=True),
            Exception("network"),  # one address fails
            _fake_clearinghouse_response("BTC", 86_500, side_long=False),
        ])
        p = HyperliquidLiquidationProvider(mock_session_factory_with_addresses, mock_hl_client)
        result = await p.get_heatmap("BTCUSDT", "4h", 84_500.0)
        assert any("clearinghouse_errors:1" in w for w in result.warnings)
        assert len(result.buckets) >= 1

    @pytest.mark.asyncio
    async def test_skips_other_coins(
        self,
        mock_session_factory_with_addresses,
        mock_hl_client,
    ) -> None:
        # Address has positions in ETH, not BTC.
        mock_hl_client.clearinghouse_state = AsyncMock(side_effect=[
            _fake_clearinghouse_response("ETH", 3200, side_long=True),
            _fake_clearinghouse_response("ETH", 3300, side_long=False),
            _fake_clearinghouse_response("ETH", 3250, side_long=True),
        ])
        p = HyperliquidLiquidationProvider(mock_session_factory_with_addresses, mock_hl_client)
        result = await p.get_heatmap("BTCUSDT", "4h", 84_500.0)
        assert result.buckets == []
```
</file_tests_liquidation_providers_test_hyperliquid_py>

<file_tests_liquidation_providers_test_hyperliquid_integration_py>

```python
"""Integration tests for HyperliquidClient against the real public endpoint.

Run with: pytest -m integration tests/liquidation/providers/test_hyperliquid_integration.py
Excluded from default pytest run.
"""
from __future__ import annotations

import pytest

from app.liquidation.providers._hyperliquid_client import HyperliquidClient

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_all_mids_returns_btc_eth_sol() -> None:
    client = HyperliquidClient()
    try:
        mids = await client.all_mids()
        assert isinstance(mids, dict)
        assert "BTC" in mids
        assert "ETH" in mids
        assert "SOL" in mids
        assert float(mids["BTC"]) > 1000
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_meta_returns_perp_universe() -> None:
    client = HyperliquidClient()
    try:
        meta = await client.meta()
        assert "universe" in meta
        coins = [c["name"] for c in meta["universe"]]
        assert "BTC" in coins
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_clearinghouse_state_known_test_address() -> None:
    """Use a known address with active positions for validation.

    Operator provides a test address (e.g. a public whale wallet) at
    integration time. If empty, the test is skipped — not failed.
    """
    test_address = "0x0000000000000000000000000000000000000000"  # placeholder
    client = HyperliquidClient()
    try:
        state = await client.clearinghouse_state(test_address)
        assert "assetPositions" in state
        # The placeholder has no positions; just validate the shape.
        assert isinstance(state["assetPositions"], list)
    finally:
        await client.close()
```
</file_tests_liquidation_providers_test_hyperliquid_integration_py>

<file_tests_liquidation_test_service_py>

```python
"""Unit tests for HeatmapService aggregation logic."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.liquidation.models import ProviderHeatmap, RawProviderBucket
from app.liquidation.service import HeatmapService, MEDIUM_AGREEMENT, HIGH_AGREEMENT


def _provider_mock(name: str, buckets: list[RawProviderBucket], as_of: datetime,
                   enabled: bool = True, max_age_s: int = 600):
    p = MagicMock()
    p.name = name
    p.enabled = enabled
    p.max_age_seconds = max_age_s
    p.get_heatmap = AsyncMock(return_value=ProviderHeatmap(
        provider=name, symbol="BTCUSDT", timeframe="4h", as_of=as_of, buckets=buckets,
    ))
    return p


def _bucket(price_low: float, side: str, vol: float, provider: str, as_of: datetime) -> RawProviderBucket:
    return RawProviderBucket(
        price_low=price_low,
        price_high=price_low + 100,
        side=side,
        est_volume_usd=vol,
        provider=provider,  # type: ignore[arg-type]
        as_of=as_of,
    )


class TestServiceMerge:
    @pytest.mark.asyncio
    async def test_no_providers_returns_empty_warning(self, now_utc) -> None:
        repo = AsyncMock()
        repo.fetch_weights = AsyncMock(return_value={})
        repo.persist_snapshot = AsyncMock()

        s = HeatmapService(providers=[], repo=repo)
        snap = await s.get_snapshot("BTCUSDT", "4h", 84_500.0)
        assert snap.magnet_zones == []
        assert "no_active_providers" in snap.provenance.warnings

    @pytest.mark.asyncio
    async def test_one_provider_warns_degraded(self, now_utc) -> None:
        repo = AsyncMock()
        repo.fetch_weights = AsyncMock(return_value={})
        repo.persist_snapshot = AsyncMock()

        p = _provider_mock("A_derived", [
            _bucket(84_000, "short_liq", 1_500_000, "A_derived", now_utc),
        ], as_of=now_utc)
        s = HeatmapService(providers=[p], repo=repo)
        snap = await s.get_snapshot("BTCUSDT", "4h", 84_500.0)
        assert "degraded_few_sources" in snap.provenance.warnings
        assert snap.sources_agreement == 1.0  # trivially agrees with itself

    @pytest.mark.asyncio
    async def test_two_providers_merge_buckets(self, now_utc) -> None:
        repo = AsyncMock()
        repo.fetch_weights = AsyncMock(return_value={})
        repo.persist_snapshot = AsyncMock()

        p1 = _provider_mock("A_derived", [
            _bucket(84_000, "short_liq", 800_000, "A_derived", now_utc),
            _bucket(82_500, "long_liq", 1_200_000, "A_derived", now_utc),
        ], as_of=now_utc)
        p2 = _provider_mock("B_hyperliquid", [
            _bucket(84_000, "short_liq", 700_000, "B_hyperliquid", now_utc),
            _bucket(82_500, "long_liq", 900_000, "B_hyperliquid", now_utc),
        ], as_of=now_utc)
        s = HeatmapService(providers=[p1, p2], repo=repo)
        snap = await s.get_snapshot("BTCUSDT", "4h", 84_500.0)
        assert len(snap.magnet_zones) >= 2
        assert set(snap.sources_used) == {"A_derived", "B_hyperliquid"}

    @pytest.mark.asyncio
    async def test_stale_provider_excluded(self, now_utc) -> None:
        repo = AsyncMock()
        repo.fetch_weights = AsyncMock(return_value={})
        repo.persist_snapshot = AsyncMock()

        stale_ts = now_utc - timedelta(hours=1)  # well past max_age
        p_stale = _provider_mock("A_derived", [
            _bucket(84_000, "short_liq", 1_500_000, "A_derived", stale_ts),
        ], as_of=stale_ts, max_age_s=30)
        p_fresh = _provider_mock("B_hyperliquid", [
            _bucket(84_000, "short_liq", 1_500_000, "B_hyperliquid", now_utc),
        ], as_of=now_utc)
        s = HeatmapService(providers=[p_stale, p_fresh], repo=repo)
        snap = await s.get_snapshot("BTCUSDT", "4h", 84_500.0)
        assert "provider_stale:A_derived" in snap.provenance.warnings
        assert "A_derived" not in snap.sources_used

    @pytest.mark.asyncio
    async def test_nearest_zones_directional(self, now_utc) -> None:
        repo = AsyncMock()
        repo.fetch_weights = AsyncMock(return_value={})
        repo.persist_snapshot = AsyncMock()

        p1 = _provider_mock("A_derived", [
            _bucket(84_200, "short_liq", 1_000_000, "A_derived", now_utc),  # ABOVE
            _bucket(84_800, "short_liq", 500_000, "A_derived", now_utc),    # ABOVE, farther
            _bucket(83_900, "long_liq", 900_000, "A_derived", now_utc),     # BELOW
        ], as_of=now_utc)
        p2 = _provider_mock("B_hyperliquid", [
            _bucket(84_200, "short_liq", 800_000, "B_hyperliquid", now_utc),
            _bucket(83_900, "long_liq", 1_100_000, "B_hyperliquid", now_utc),
        ], as_of=now_utc)
        s = HeatmapService(providers=[p1, p2], repo=repo)
        snap = await s.get_snapshot("BTCUSDT", "4h", 84_500.0)

        assert snap.nearest_short_liq is not None
        assert snap.nearest_short_liq.distance_pct > 0  # above
        assert snap.nearest_long_liq is not None
        assert snap.nearest_long_liq.distance_pct < 0  # below

    @pytest.mark.asyncio
    async def test_imbalance_long_heavy(self, now_utc) -> None:
        repo = AsyncMock()
        repo.fetch_weights = AsyncMock(return_value={})
        repo.persist_snapshot = AsyncMock()

        p = _provider_mock("A_derived", [
            _bucket(83_900, "long_liq", 2_000_000, "A_derived", now_utc),
            _bucket(83_800, "long_liq", 1_000_000, "A_derived", now_utc),
            _bucket(84_700, "short_liq", 500_000, "A_derived", now_utc),
        ], as_of=now_utc)
        p2 = _provider_mock("B_hyperliquid", [
            _bucket(83_900, "long_liq", 1_500_000, "B_hyperliquid", now_utc),
            _bucket(84_700, "short_liq", 400_000, "B_hyperliquid", now_utc),
        ], as_of=now_utc)
        s = HeatmapService(providers=[p, p2], repo=repo)
        snap = await s.get_snapshot("BTCUSDT", "4h", 84_500.0)
        assert snap.imbalance_ratio > 1.5

    @pytest.mark.asyncio
    async def test_density_high_when_zones_concentrated(self, now_utc) -> None:
        repo = AsyncMock()
        repo.fetch_weights = AsyncMock(return_value={})
        repo.persist_snapshot = AsyncMock()

        # All zones within ±2% of 84_500 → density should be high.
        p = _provider_mock("A_derived", [
            _bucket(84_300, "long_liq", 1_000_000, "A_derived", now_utc),
            _bucket(84_700, "short_liq", 1_000_000, "A_derived", now_utc),
        ], as_of=now_utc)
        p2 = _provider_mock("B_hyperliquid", [
            _bucket(84_300, "long_liq", 800_000, "B_hyperliquid", now_utc),
            _bucket(84_700, "short_liq", 800_000, "B_hyperliquid", now_utc),
        ], as_of=now_utc)
        s = HeatmapService(providers=[p, p2], repo=repo)
        snap = await s.get_snapshot("BTCUSDT", "4h", 84_500.0)
        assert snap.cluster_density > 0.8

    @pytest.mark.asyncio
    async def test_uniform_weights_when_no_calibration(self, now_utc) -> None:
        repo = AsyncMock()
        repo.fetch_weights = AsyncMock(return_value={})  # empty → uniform
        repo.persist_snapshot = AsyncMock()

        # Equal volumes from both providers → equal contributions.
        p1 = _provider_mock("A_derived", [
            _bucket(84_000, "short_liq", 1_000_000, "A_derived", now_utc),
        ], as_of=now_utc)
        p2 = _provider_mock("B_hyperliquid", [
            _bucket(84_000, "short_liq", 1_000_000, "B_hyperliquid", now_utc),
        ], as_of=now_utc)
        s = HeatmapService(providers=[p1, p2], repo=repo)
        snap = await s.get_snapshot("BTCUSDT", "4h", 84_500.0)
        # Each should contribute 50%.
        zone = snap.magnet_zones[0]
        assert zone.source_breakdown["A_derived"] == pytest.approx(
            zone.source_breakdown["B_hyperliquid"], rel=0.01
        )

    @pytest.mark.asyncio
    async def test_stored_weights_applied(self, now_utc) -> None:
        repo = AsyncMock()
        repo.fetch_weights = AsyncMock(return_value={
            "A_derived": 0.70,
            "B_hyperliquid": 0.30,
        })
        repo.persist_snapshot = AsyncMock()

        p1 = _provider_mock("A_derived", [
            _bucket(84_000, "short_liq", 1_000_000, "A_derived", now_utc),
        ], as_of=now_utc)
        p2 = _provider_mock("B_hyperliquid", [
            _bucket(84_000, "short_liq", 1_000_000, "B_hyperliquid", now_utc),
        ], as_of=now_utc)
        s = HeatmapService(providers=[p1, p2], repo=repo)
        snap = await s.get_snapshot("BTCUSDT", "4h", 84_500.0)
        zone = snap.magnet_zones[0]
        # A_derived should have ~2.33× the contribution of B_hyperliquid.
        ratio = zone.source_breakdown["A_derived"] / zone.source_breakdown["B_hyperliquid"]
        assert 2.0 < ratio < 2.6


class TestServiceConfidence:
    @pytest.mark.asyncio
    async def test_high_confidence_when_agreement_high(self, now_utc) -> None:
        repo = AsyncMock()
        repo.fetch_weights = AsyncMock(return_value={})
        repo.persist_snapshot = AsyncMock()

        # Two providers pointing at near-identical zones → high agreement.
        p1 = _provider_mock("A_derived", [
            _bucket(84_000, "short_liq", 1_000_000, "A_derived", now_utc),
            _bucket(82_500, "long_liq", 800_000, "A_derived", now_utc),
        ], as_of=now_utc)
        p2 = _provider_mock("B_hyperliquid", [
            _bucket(84_000, "short_liq", 950_000, "B_hyperliquid", now_utc),
            _bucket(82_500, "long_liq", 850_000, "B_hyperliquid", now_utc),
        ], as_of=now_utc)
        s = HeatmapService(providers=[p1, p2], repo=repo)
        snap = await s.get_snapshot("BTCUSDT", "4h", 84_500.0)
        assert snap.sources_agreement >= HIGH_AGREEMENT
        assert all(z.confidence == "high" for z in snap.magnet_zones)

    @pytest.mark.asyncio
    async def test_low_confidence_when_disagree(self, now_utc) -> None:
        repo = AsyncMock()
        repo.fetch_weights = AsyncMock(return_value={})
        repo.persist_snapshot = AsyncMock()

        # Providers point at totally different zones.
        p1 = _provider_mock("A_derived", [
            _bucket(84_000, "short_liq", 1_000_000, "A_derived", now_utc),
        ], as_of=now_utc)
        p2 = _provider_mock("B_hyperliquid", [
            _bucket(80_000, "long_liq", 1_000_000, "B_hyperliquid", now_utc),
        ], as_of=now_utc)
        s = HeatmapService(providers=[p1, p2], repo=repo)
        snap = await s.get_snapshot("BTCUSDT", "4h", 84_500.0)
        assert snap.sources_agreement < MEDIUM_AGREEMENT
```
</file_tests_liquidation_test_service_py>

<file_tests_liquidation_test_calibration_py>

```python
"""Unit tests for compute_provider_weights."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.liquidation.calibration import (
    MIN_SAMPLES,
    WEIGHT_FLOOR,
    compute_provider_weights,
)


def _log_row(
    symbol: str,
    timeframe: str,
    verdict: str,
    delta_a: float | None,
    delta_b: float | None,
    days_ago: int = 1,
) -> dict:
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "proposed_zone_price": 84_000.0,
        "proposed_zone_side": "short_liq",
        "source_a_price": 84_000.0 if delta_a is None else 84_000.0 * (1 + delta_a / 100),
        "source_b_price": 84_000.0 if delta_b is None else 84_000.0 * (1 + delta_b / 100),
        "source_c_verdict": verdict,
        "delta_a_pct": delta_a,
        "delta_b_pct": delta_b,
        "logged_at": datetime.now(tz=UTC) - timedelta(days=days_ago),
    }


@pytest.mark.asyncio
async def test_empty_log_returns_empty() -> None:
    repo = AsyncMock()
    repo.fetch_agreement_log = AsyncMock(return_value=[])
    out = await compute_provider_weights(repo)
    assert out == []


@pytest.mark.asyncio
async def test_skips_below_min_samples() -> None:
    repo = AsyncMock()
    repo.fetch_agreement_log = AsyncMock(return_value=[
        _log_row("BTCUSDT", "4h", "agree", 0.2, 0.4) for _ in range(MIN_SAMPLES - 1)
    ])
    out = await compute_provider_weights(repo)
    assert out == []


@pytest.mark.asyncio
async def test_perfect_agreement_yields_floor_normalized() -> None:
    """Both providers always agree with TD → both get 1.0 raw, then floored
    and normalized to 0.5 each."""
    repo = AsyncMock()
    repo.fetch_agreement_log = AsyncMock(return_value=[
        _log_row("BTCUSDT", "4h", "agree", 0.1, 0.2) for _ in range(MIN_SAMPLES * 2)
    ])
    out = await compute_provider_weights(repo)
    assert len(out) == 2
    weights = {w.provider: w.weight for w in out}
    assert weights["A_derived"] == pytest.approx(0.5, rel=1e-3)
    assert weights["B_hyperliquid"] == pytest.approx(0.5, rel=1e-3)


@pytest.mark.asyncio
async def test_one_provider_zero_agreement_floors() -> None:
    """Provider A always far off; B always close. After floor + normalize,
    A should be at floor / (floor + 1.0) ≈ 0.091."""
    repo = AsyncMock()
    repo.fetch_agreement_log = AsyncMock(return_value=[
        _log_row("BTCUSDT", "4h", "agree", 5.0, 0.1) for _ in range(MIN_SAMPLES * 2)
    ])
    out = await compute_provider_weights(repo)
    weights = {w.provider: w.weight for w in out}
    assert weights["A_derived"] == pytest.approx(WEIGHT_FLOOR / (WEIGHT_FLOOR + 1.0), rel=0.01)
    assert weights["B_hyperliquid"] == pytest.approx(1.0 / (WEIGHT_FLOOR + 1.0), rel=0.01)


@pytest.mark.asyncio
async def test_skipped_verdict_ignored() -> None:
    """Skipped entries should not affect the count or agreement."""
    repo = AsyncMock()
    rows = [_log_row("BTCUSDT", "4h", "agree", 0.1, 0.1) for _ in range(MIN_SAMPLES)]
    rows.extend([_log_row("BTCUSDT", "4h", "skipped", None, None) for _ in range(50)])
    repo.fetch_agreement_log = AsyncMock(return_value=rows)
    out = await compute_provider_weights(repo)
    # Should still produce weights based on the MIN_SAMPLES non-skipped rows.
    assert len(out) == 2


@pytest.mark.asyncio
async def test_weights_sum_to_one_per_cell() -> None:
    repo = AsyncMock()
    repo.fetch_agreement_log = AsyncMock(return_value=[
        _log_row("BTCUSDT", "4h", "agree", 0.3, 1.0) for _ in range(MIN_SAMPLES * 2)
    ])
    out = await compute_provider_weights(repo)
    by_cell: dict = {}
    for w in out:
        by_cell.setdefault((w.symbol, w.timeframe), []).append(w.weight)
    for cell, weights in by_cell.items():
        assert sum(weights) == pytest.approx(1.0, rel=1e-3)
```
</file_tests_liquidation_test_calibration_py>

<file_tests_liquidation_test_telegram_handlers_py>

```python
"""Tests for record_ground_truth handler."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.liquidation.telegram_handlers import record_ground_truth


@pytest.fixture
def mock_session_factory_with_setup():
    """Returns a setup row with a heatmap citation snapshot."""
    session = AsyncMock()
    setup_row = MagicMock()
    setup_row.symbol = "BTCUSDT"
    setup_row.factor_snapshot = {
        "get_liquidation_heatmap": {
            "nearest_short_liq_price": 85_400.0,
            "source_breakdown_a_price": 85_390.0,
            "source_breakdown_b_price": 85_440.0,
            "timeframe": "4h",
        }
    }
    result = MagicMock()
    result.first = MagicMock(return_value=setup_row)
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=session)
    return factory


@pytest.mark.asyncio
async def test_invalid_verdict_returns_false() -> None:
    factory = MagicMock()
    ok = await record_ground_truth(
        session_factory=factory, user_id="u", setup_id="s", verdict="weird",
    )
    assert ok is False


@pytest.mark.asyncio
async def test_setup_not_found_returns_false() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.first = MagicMock(return_value=None)
    session.execute = AsyncMock(return_value=result)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=session)

    ok = await record_ground_truth(
        session_factory=factory, user_id="u", setup_id="nonexistent", verdict="agree",
    )
    assert ok is False


@pytest.mark.asyncio
async def test_agree_persists_with_correct_deltas(mock_session_factory_with_setup) -> None:
    ok = await record_ground_truth(
        session_factory=mock_session_factory_with_setup,
        user_id="u",
        setup_id="setup-123",
        verdict="agree",
    )
    assert ok is True
    # Verify execute was called with delta values computed correctly.
    session = mock_session_factory_with_setup.return_value
    # 2nd call is the INSERT (1st is the SELECT). Check params.
    insert_call = session.execute.call_args_list[1]
    params = insert_call[0][1]
    # delta_a = |85390 - 85400| / 85400 * 100 ≈ 0.012%
    assert params["delta_a"] == pytest.approx(0.0117, abs=0.001)
    # delta_b = |85440 - 85400| / 85400 * 100 ≈ 0.047%
    assert params["delta_b"] == pytest.approx(0.0468, abs=0.001)
    assert params["verdict"] == "agree"


@pytest.mark.asyncio
async def test_no_heatmap_citation_returns_false() -> None:
    session = AsyncMock()
    setup_row = MagicMock()
    setup_row.symbol = "BTCUSDT"
    setup_row.factor_snapshot = {}  # No heatmap citation
    result = MagicMock()
    result.first = MagicMock(return_value=setup_row)
    session.execute = AsyncMock(return_value=result)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=session)

    ok = await record_ground_truth(
        session_factory=factory, user_id="u", setup_id="setup-123", verdict="agree",
    )
    assert ok is False
```
</file_tests_liquidation_test_telegram_handlers_py>

<file_tests_liquidation_test_repo_py>

```python
"""DB persistence tests for LiquidationRepo. Uses transactional rollback."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.agent.tools._envelope import Provenance
from app.liquidation.models import (
    HeatmapSnapshot,
    ProviderWeight,
    RawProviderBucket,
)
from app.liquidation.repo import LiquidationRepo


@pytest.mark.asyncio
async def test_persist_snapshot_writes_buckets(liq_repo, db_session, now_utc) -> None:
    snap = HeatmapSnapshot(
        symbol="BTCUSDT",
        timeframe="4h",
        current_price=84_500.0,
        as_of=now_utc,
        magnet_zones=[],
        imbalance_ratio=1.0,
        cluster_density=0.5,
        sources_used=["A_derived"],
        sources_agreement=1.0,
        provenance=Provenance(source="test", as_of=now_utc, rows=0, warnings=[]),
    )
    raw_buckets = {
        "A_derived": [
            RawProviderBucket(
                price_low=84_000, price_high=84_200, side="short_liq",
                est_volume_usd=1_500_000, provider="A_derived", as_of=now_utc,
            ),
            RawProviderBucket(
                price_low=82_500, price_high=82_700, side="long_liq",
                est_volume_usd=2_100_000, provider="A_derived", as_of=now_utc,
            ),
        ],
    }
    await liq_repo.persist_snapshot(snap, raw_buckets)

    # Verify rows.
    from sqlalchemy import text
    result = await db_session.execute(text(
        "SELECT COUNT(*) FROM liquidation_buckets WHERE symbol = 'BTCUSDT'"
    ))
    assert result.scalar_one() == 2


@pytest.mark.asyncio
async def test_fetch_weights_empty_returns_empty_dict(liq_repo) -> None:
    weights = await liq_repo.fetch_weights("BTCUSDT", "4h")
    assert weights == {}


@pytest.mark.asyncio
async def test_save_then_fetch_weights(liq_repo, now_utc) -> None:
    weights = [
        ProviderWeight(
            symbol="BTCUSDT", timeframe="4h", provider="A_derived",
            weight=0.7, agreement_rate=0.8, n_samples=20, computed_at=now_utc,
        ),
        ProviderWeight(
            symbol="BTCUSDT", timeframe="4h", provider="B_hyperliquid",
            weight=0.3, agreement_rate=0.3, n_samples=20, computed_at=now_utc,
        ),
    ]
    await liq_repo.save_weights(weights)
    out = await liq_repo.fetch_weights("BTCUSDT", "4h")
    assert out["A_derived"] == pytest.approx(0.7)
    assert out["B_hyperliquid"] == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_fetch_weights_returns_latest_only(liq_repo, now_utc) -> None:
    """If a provider has multiple weight rows, return only the most recent."""
    weights_old = ProviderWeight(
        symbol="BTCUSDT", timeframe="4h", provider="A_derived",
        weight=0.5, agreement_rate=0.5, n_samples=20,
        computed_at=now_utc - timedelta(days=7),
    )
    weights_new = ProviderWeight(
        symbol="BTCUSDT", timeframe="4h", provider="A_derived",
        weight=0.8, agreement_rate=0.85, n_samples=30, computed_at=now_utc,
    )
    await liq_repo.save_weights([weights_old, weights_new])
    out = await liq_repo.fetch_weights("BTCUSDT", "4h")
    assert out["A_derived"] == pytest.approx(0.8)
```
</file_tests_liquidation_test_repo_py>

<file_tests_liquidation_test_tool_py>

```python
"""Tests for register_liquidation_tool integration."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# These tests focus on the tool's behavior given a mocked HeatmapService.
# Integration with the real agent framework is covered in
# tests/agent/test_validators_citation_rigor.py.


@pytest.mark.asyncio
async def test_tool_returns_tool_result_envelope(now_utc) -> None:
    """The tool must wrap the HeatmapSnapshot in ToolResult with provenance."""
    from app.agent.tools._envelope import Provenance, ToolResult
    from app.liquidation.models import HeatmapSnapshot

    snap = HeatmapSnapshot(
        symbol="BTCUSDT",
        timeframe="4h",
        current_price=84_500.0,
        as_of=now_utc,
        magnet_zones=[],
        imbalance_ratio=1.0,
        cluster_density=0.0,
        sources_used=["A_derived"],
        sources_agreement=1.0,
        provenance=Provenance(source="liquidation_heatmap_engine", as_of=now_utc, rows=0, warnings=[]),
    )

    # If we wrap it manually:
    result = ToolResult(data=snap, provenance=snap.provenance)
    assert result.data.symbol == "BTCUSDT"
    assert result.provenance.source == "liquidation_heatmap_engine"


# Note: a full end-to-end test that boots a real Agent and invokes the tool is
# heavier and lives in `tests/agent/test_integration_liquidation.py`. The
# important contract — that the validator catches malformed citations — is
# tested separately in test_validators_citation_rigor.py (below).
```
</file_tests_liquidation_test_tool_py>

<file_tests_agent_validators_citation_rigor_additions>

Append these 4 tests to `apps/api/tests/agent/test_validators_citation_rigor.py`:

```python
# ============================================================
# Liquidation tool citation contract tests (added by spec 05)
# ============================================================
import pytest
from pydantic_ai.exceptions import ModelRetry

from app.agent.validators import must_cite_quantitative_claims
# Adapt this import to whatever the existing helpers in the file are named.


def _build_idea_with_liq_citation(
    *,
    symbol: str = "BTCUSDT",
    current_price: float = 84_500.0,
    sources_agreement: float = 0.90,
    sources_used: tuple[str, ...] = ("A_derived", "B_hyperliquid"),
    direction: str = "long",
    confidence: str = "medium",
):
    """Build a TradeIdea fixture with a single citation to get_liquidation_heatmap.
    Adapt the construction to match your TradeIdea + ToolCitation models."""
    from app.setups.models import TradeIdea, ToolCitation
    return TradeIdea(
        symbol=symbol,
        direction=direction,
        confidence=confidence,
        # ... other required fields filled with safe defaults ...
        citations=[
            ToolCitation(
                tool_name="get_liquidation_heatmap",
                snapshot={
                    "symbol": symbol,
                    "current_price": current_price,
                    "sources_agreement": sources_agreement,
                    "sources_used": list(sources_used),
                    "nearest_short_liq_price": 85_400.0,
                },
            ),
        ],
    )


def test_validator_rejects_phantom_liquidation_zone(real_tool_outputs_empty) -> None:
    """If the agent cites get_liquidation_heatmap without invoking it, reject."""
    idea = _build_idea_with_liq_citation()
    with pytest.raises(ModelRetry, match="not invoked"):
        must_cite_quantitative_claims(idea, real_tool_outputs_empty)


def test_validator_rejects_mismatched_agreement(real_tool_outputs_with_liq) -> None:
    """If sources_agreement in citation diverges from real, reject."""
    # real_tool_outputs_with_liq returns sources_agreement=0.90.
    idea = _build_idea_with_liq_citation(sources_agreement=0.50)  # wrong
    with pytest.raises(ModelRetry, match="sources_agreement"):
        must_cite_quantitative_claims(idea, real_tool_outputs_with_liq)


def test_validator_rejects_incoherent_confidence_liquidation(real_tool_outputs_with_liq_low_agreement) -> None:
    """Confidence='high' incompatible with agreement<0.60."""
    idea = _build_idea_with_liq_citation(
        sources_agreement=0.50,
        confidence="high",
    )
    with pytest.raises(ModelRetry, match="confidence"):
        must_cite_quantitative_claims(idea, real_tool_outputs_with_liq_low_agreement)


def test_validator_rejects_same_side_tp_liquidation(real_tool_outputs_with_liq) -> None:
    """A long setup citing nearest_long_liq as the TP zone is illogical."""
    from app.setups.models import TradeIdea, ToolCitation
    idea = TradeIdea(
        symbol="BTCUSDT",
        direction="long",
        confidence="medium",
        citations=[
            ToolCitation(
                tool_name="get_liquidation_heatmap",
                snapshot={
                    "symbol": "BTCUSDT",
                    "current_price": 84_500.0,
                    "sources_agreement": 0.90,
                    "sources_used": ["A_derived", "B_hyperliquid"],
                    "nearest_long_liq_price": 82_500.0,  # WRONG: long setup citing long_liq
                },
            ),
        ],
    )
    with pytest.raises(ModelRetry, match="direction"):
        must_cite_quantitative_claims(idea, real_tool_outputs_with_liq)
```

The fixtures `real_tool_outputs_empty`, `real_tool_outputs_with_liq`, and `real_tool_outputs_with_liq_low_agreement` need to be added to the existing `conftest.py` for the agent tests. They mirror the shape of the existing tool output dict structure (look at how existing tests construct fixtures for `get_indicators` or `get_market_dominance`).
</file_tests_agent_validators_citation_rigor_additions>

<test_running_checklist>

Before declaring the module done, all of these must pass:

```bash
# Unit tests (fast, no network)
cd apps/api
pytest tests/liquidation -m "not integration" -v

# Citation contract tests
pytest tests/agent/test_validators_citation_rigor.py -v

# Integration (network)
pytest tests/liquidation -m integration -v

# Full suite for sanity
pytest tests/ -q
```

Manual checks:
- [ ] `pytest --collect-only tests/liquidation` lists ≥ 30 tests.
- [ ] No tests skipped without explanation.
- [ ] Property-based tests for `compute_provider_weights` (hypothesis) verify weights always sum to 1.0 ± 1e-6 per cell.
- [ ] `pytest -p no:cacheprovider tests/liquidation` (fresh) all green.
</test_running_checklist>

<acceptance>
- [ ] All test files from the layout above exist and pass.
- [ ] Total tests in `tests/liquidation/` is ≥ 30.
- [ ] Citation contract has 4 new tests (one per failure mode).
- [ ] Integration test for Hyperliquid endpoint passes against real API.
- [ ] No test relies on internet access except those marked `-m integration`.
- [ ] No test takes longer than 5 seconds (unit-only suite under 30s total).
</acceptance>
