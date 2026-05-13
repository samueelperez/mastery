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
        # 10x leverage with mm=0.05: 100 * (1 + 0.1 - 0.05) = 105.
        liq = estimate_liq_price(100.0, "B", 10)
        assert 104 < liq < 106
        assert liq > 100  # liquidation IS above the entry

    def test_long_counterparty_liq_below_trade_price(self) -> None:
        # 10x leverage with mm=0.05: 100 * (1 - 0.1 + 0.05) = 95.
        liq = estimate_liq_price(100.0, "S", 10)
        assert 94 < liq < 96
        assert liq < 100  # liquidation IS below the entry

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
