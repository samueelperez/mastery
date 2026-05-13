"""Tests for `record_ground_truth`.

Mocks the session factory; verifies SQL bindings + delta math + edge cases
(invalid verdict, missing setup, missing heatmap citation).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.liquidation.telegram_handlers import record_ground_truth


def _session_factory_with_setup(
    *,
    factor_snapshot: dict | None = None,
    found: bool = True,
) -> MagicMock:
    """Build a session_factory whose first SELECT returns a setup row.

    `factor_snapshot` controls the `journal_trades.factor_snapshot` jsonb
    value. `found=False` simulates a setup-not-found row.
    """
    session = AsyncMock()

    setup_row = None
    if found:
        setup_row = MagicMock()
        setup_row.symbol = "BTCUSDT"
        setup_row.factor_snapshot = factor_snapshot

    select_result = MagicMock()
    select_result.first = MagicMock(return_value=setup_row)
    # First execute returns the SELECT; later ones (INSERT) just return a
    # generic mock that doesn't need to be inspected.
    insert_result = MagicMock()
    session.execute = AsyncMock(side_effect=[select_result, insert_result])
    session.commit = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    factory = MagicMock(return_value=session)
    return factory


@pytest.fixture
def now_utc_fixture() -> datetime:
    return datetime(2026, 5, 13, 12, 0, 0, tzinfo=UTC)


async def test_invalid_verdict_returns_false() -> None:
    factory = MagicMock()
    ok = await record_ground_truth(
        session_factory=factory, user_id="u", setup_id="s", verdict="weird"
    )
    assert ok is False
    # The factory is never invoked when verdict fails the gate.
    factory.assert_not_called()


async def test_setup_not_found_returns_false() -> None:
    factory = _session_factory_with_setup(found=False)
    ok = await record_ground_truth(
        session_factory=factory,
        user_id="u",
        setup_id="nonexistent",
        verdict="agree",
    )
    assert ok is False


async def test_no_heatmap_citation_returns_false() -> None:
    factory = _session_factory_with_setup(factor_snapshot={})
    ok = await record_ground_truth(
        session_factory=factory,
        user_id="u",
        setup_id="setup-123",
        verdict="agree",
    )
    assert ok is False


async def test_no_proposed_zone_returns_false() -> None:
    """Citation has neither nearest_short nor nearest_long → can't persist."""
    factory = _session_factory_with_setup(
        factor_snapshot={
            "get_liquidation_heatmap": {
                # No nearest_*_liq_price keys
                "timeframe": "4h",
            }
        }
    )
    ok = await record_ground_truth(
        session_factory=factory,
        user_id="u",
        setup_id="setup-123",
        verdict="agree",
    )
    assert ok is False


async def test_agree_persists_with_correct_deltas() -> None:
    factory = _session_factory_with_setup(
        factor_snapshot={
            "get_liquidation_heatmap": {
                "nearest_short_liq_price": 85_400.0,
                "source_breakdown_a_price": 85_390.0,
                "source_breakdown_b_price": 85_440.0,
                "timeframe": "4h",
            }
        }
    )
    ok = await record_ground_truth(
        session_factory=factory,
        user_id="u",
        setup_id="setup-123",
        verdict="agree",
    )
    assert ok is True

    session = factory.return_value
    insert_call = session.execute.call_args_list[1]
    params = insert_call[0][1]
    # |85390 - 85400| / 85400 * 100 ≈ 0.01171
    assert params["delta_a"] == pytest.approx(0.01171, abs=0.001)
    # |85440 - 85400| / 85400 * 100 ≈ 0.04684
    assert params["delta_b"] == pytest.approx(0.04684, abs=0.001)
    assert params["verdict"] == "agree"
    assert params["proposed_price"] == 85_400.0
    assert params["proposed_side"] == "short_liq"
    assert params["symbol"] == "BTCUSDT"
    assert params["timeframe"] == "4h"


async def test_long_setup_persists_long_liq_side() -> None:
    """Citation has nearest_long_liq_price (short setup) → side=long_liq."""
    factory = _session_factory_with_setup(
        factor_snapshot={
            "get_liquidation_heatmap": {
                "nearest_long_liq_price": 78_000.0,
                "source_breakdown_a_price": 78_050.0,
                "source_breakdown_b_price": 77_960.0,
                "timeframe": "1h",
            }
        }
    )
    ok = await record_ground_truth(
        session_factory=factory,
        user_id="u",
        setup_id="s",
        verdict="disagree",
    )
    assert ok is True

    params = factory.return_value.execute.call_args_list[1][0][1]
    assert params["proposed_side"] == "long_liq"
    assert params["proposed_price"] == 78_000.0
    assert params["verdict"] == "disagree"
    assert params["timeframe"] == "1h"


async def test_missing_source_breakdown_yields_none_deltas() -> None:
    """Only nearest_short present → deltas come out None (provider data N/A)."""
    factory = _session_factory_with_setup(
        factor_snapshot={
            "get_liquidation_heatmap": {
                "nearest_short_liq_price": 85_000.0,
                "timeframe": "4h",
            }
        }
    )
    ok = await record_ground_truth(
        session_factory=factory,
        user_id="u",
        setup_id="s",
        verdict="close",
    )
    assert ok is True

    params = factory.return_value.execute.call_args_list[1][0][1]
    assert params["delta_a"] is None
    assert params["delta_b"] is None


async def test_close_verdict_accepted() -> None:
    factory = _session_factory_with_setup(
        factor_snapshot={
            "get_liquidation_heatmap": {
                "nearest_short_liq_price": 85_400.0,
                "source_breakdown_a_price": 85_400.0,
                "source_breakdown_b_price": 85_400.0,
                "timeframe": "4h",
            }
        }
    )
    ok = await record_ground_truth(
        session_factory=factory,
        user_id="u",
        setup_id="s",
        verdict="close",
    )
    assert ok is True
