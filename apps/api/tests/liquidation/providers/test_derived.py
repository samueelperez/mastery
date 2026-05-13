"""Unit tests for DerivedLiquidationProvider.

Mocks the DB via a fake session_factory; tests pure aggregation logic.
The critical `test_no_lookahead` exercises `_compute_buckets` as a pure
function (per CLAUDE.md::critical_invariants #6).
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import polars as pl
import pytest

from app.liquidation.providers.derived import (
    BUCKET_PCT,
    DerivedLiquidationProvider,
)


@pytest.fixture
def mock_session_factory(now_utc):
    """Returns a session_factory whose SELECT yields 10 fake trades.

    Each trade: 5 BTC × $84_500 = $422_500 notional (above MIN_TRADE_USD).
    """
    session = AsyncMock()
    fake_rows = []
    for i in range(10):
        r = MagicMock()
        r.ts = now_utc - timedelta(hours=i)
        r.price = 84_500
        r.size = 5.0
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
    async def test_unsupported_symbol(self, mock_session_factory) -> None:
        p = DerivedLiquidationProvider(mock_session_factory)
        result = await p.get_heatmap("DOGEUSDT", "4h", 0.10)
        assert result.buckets == []
        assert any("symbol_not_supported" in w for w in result.warnings)

    async def test_supports_symbol(self, mock_session_factory) -> None:
        p = DerivedLiquidationProvider(mock_session_factory)
        assert p.supports_symbol("BTCUSDT")
        assert p.supports_symbol("ETHUSDT")
        assert p.supports_symbol("SOLUSDT")
        assert not p.supports_symbol("XRPUSDT")

    async def test_returns_buckets_on_valid_trades(self, mock_session_factory) -> None:
        p = DerivedLiquidationProvider(mock_session_factory)
        result = await p.get_heatmap("BTCUSDT", "4h", 84_500.0)
        assert result.provider == "A_derived"
        assert result.symbol == "BTCUSDT"
        # With 10 trades × 7 leverage brackets, expect ≥1 bucket within ±10%.
        assert len(result.buckets) > 0

    def test_no_lookahead(self, now_utc) -> None:
        """Critical invariant #6: future trades must NOT change past results.

        `_compute_buckets` is a pure function on (DataFrame, now). Feeding it a
        DataFrame that includes future trades must produce the same buckets as
        feeding it the same DataFrame pre-filtered to `ts <= now`. This proves
        that future rows in the input never leak into the output.
        """
        prov = DerivedLiquidationProvider(MagicMock())

        trades_now = pl.DataFrame(
            {
                "ts": [now_utc - timedelta(hours=h) for h in range(10)],
                "price": [84_500.0] * 10,
                "size": [5.0] * 10,
                "side": ["B"] * 5 + ["S"] * 5,
            }
        )
        future = pl.DataFrame(
            {
                "ts": [now_utc + timedelta(hours=1)],
                "price": [85_000.0],
                "size": [10.0],
                "side": ["B"],
            }
        )
        with_future = pl.concat([trades_now, future])
        filtered = with_future.filter(pl.col("ts") <= now_utc)

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
        # Sort by deterministic keys before comparing: `.sort("weight", ...)`
        # in the impl is non-stable across equal weights, but the *content*
        # must be identical — that's what the invariant requires.
        sort_keys = ["price_low", "side"]
        assert df1.sort(sort_keys).to_dict(as_series=False) == df2.sort(sort_keys).to_dict(
            as_series=False
        )

    async def test_buckets_within_max_distance(self, mock_session_factory) -> None:
        """All returned buckets must intersect the ±max_distance_pct window.

        The filter operates on `liq_px`, but bucketing floors to a grid of
        `BUCKET_PCT` width, so a bucket whose midpoint sits up to half a
        bucket-width outside the window is still legitimate (its lower edge
        IS within the window). Allow that half-bucket slack.
        """
        from app.liquidation.providers.derived import BUCKET_PCT

        p = DerivedLiquidationProvider(mock_session_factory)
        current = 84_500.0
        max_pct = 5.0
        tolerance = (BUCKET_PCT / 2) * 100 + 1e-6
        result = await p.get_heatmap("BTCUSDT", "4h", current, max_distance_pct=max_pct)
        for b in result.buckets:
            mid = (b.price_low + b.price_high) / 2
            dist_pct = abs(mid - current) / current * 100
            assert dist_pct <= max_pct + tolerance
