"""DSL validation tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.alerts.dsl import Condition, RuleSpec


def test_rulespec_normalizes_symbol_to_upper() -> None:
    spec = RuleSpec(
        symbol="btcusdt",
        timeframe="4h",
        conditions=[Condition(left="rsi_14", op="<=", right=30)],
    )
    assert spec.symbol == "BTCUSDT"


def test_rulespec_requires_at_least_one_condition() -> None:
    with pytest.raises(ValidationError):
        RuleSpec(symbol="BTCUSDT", timeframe="4h", conditions=[])


def test_condition_rejects_self_comparison() -> None:
    with pytest.raises(ValidationError):
        Condition(left="ema_21", op=">", right="ema_21")


def test_rulespec_default_logic_is_all() -> None:
    spec = RuleSpec(
        symbol="BTCUSDT",
        timeframe="4h",
        conditions=[Condition(left="rsi_14", op="<=", right=30)],
    )
    assert spec.logic == "all"
