"""Pure-function evaluator tests over hand-built panels."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl

from app.alerts.dsl import Condition, RuleSpec
from app.alerts.evaluator import build_snapshot, evaluate_rule


def _panel(rows: list[dict[str, float]]) -> pl.DataFrame:
    """Build a synthetic panel; ts auto-incremented hourly so we can test
    cross_above / cross_below across two bars."""
    base = datetime(2026, 5, 4, 0, 0, tzinfo=UTC)
    data = {"ts": [base + timedelta(hours=i) for i in range(len(rows))]}
    keys = {k for r in rows for k in r}
    for k in keys:
        data[k] = [float(r.get(k, float("nan"))) for r in rows]
    return pl.DataFrame(data)


def test_simple_threshold_fires() -> None:
    panel = _panel([{"c": 100, "rsi_14": 25}])
    spec = RuleSpec(
        symbol="BTCUSDT",
        timeframe="4h",
        conditions=[Condition(left="rsi_14", op="<=", right=30)],
    )
    assert evaluate_rule(spec, panel) is True


def test_logic_all_requires_every_condition() -> None:
    panel = _panel([{"c": 100, "rsi_14": 28, "ema_21": 99}])
    spec = RuleSpec(
        symbol="BTCUSDT",
        timeframe="4h",
        conditions=[
            Condition(left="rsi_14", op="<=", right=30),
            Condition(left="ema_21", op=">", right=200),  # fails
        ],
        logic="all",
    )
    assert evaluate_rule(spec, panel) is False


def test_logic_any_short_circuits() -> None:
    panel = _panel([{"c": 100, "rsi_14": 50, "ema_21": 99}])
    spec = RuleSpec(
        symbol="BTCUSDT",
        timeframe="4h",
        conditions=[
            Condition(left="rsi_14", op="<=", right=30),  # fails
            Condition(left="ema_21", op="<", right=200),  # fires
        ],
        logic="any",
    )
    assert evaluate_rule(spec, panel) is True


def test_cross_above_uses_two_bars() -> None:
    # Previous bar: ema_21 < ema_55. Current bar: ema_21 >= ema_55.
    panel = _panel(
        [
            {"c": 100, "ema_21": 95, "ema_55": 100},
            {"c": 102, "ema_21": 102, "ema_55": 100},
        ]
    )
    spec = RuleSpec(
        symbol="BTCUSDT",
        timeframe="4h",
        conditions=[Condition(left="ema_21", op="cross_above", right="ema_55")],
    )
    assert evaluate_rule(spec, panel) is True


def test_cross_above_does_not_fire_without_crossing() -> None:
    # Both bars above — no cross.
    panel = _panel(
        [
            {"c": 100, "ema_21": 105, "ema_55": 100},
            {"c": 102, "ema_21": 110, "ema_55": 100},
        ]
    )
    spec = RuleSpec(
        symbol="BTCUSDT",
        timeframe="4h",
        conditions=[Condition(left="ema_21", op="cross_above", right="ema_55")],
    )
    assert evaluate_rule(spec, panel) is False


def test_compares_against_ohlcv_column() -> None:
    panel = _panel([{"c": 99, "ema_21": 100}])
    spec = RuleSpec(
        symbol="BTCUSDT",
        timeframe="4h",
        conditions=[Condition(left="c", op="<", right="ema_21")],
    )
    assert evaluate_rule(spec, panel) is True


def test_missing_column_returns_false_not_error() -> None:
    panel = _panel([{"c": 100}])
    spec = RuleSpec(
        symbol="BTCUSDT",
        timeframe="4h",
        conditions=[Condition(left="rsi_14", op="<=", right=30)],
    )
    assert evaluate_rule(spec, panel) is False


def test_snapshot_includes_matched_conditions_and_values() -> None:
    panel = _panel([{"c": 100, "rsi_14": 25, "ema_21": 99}])
    spec = RuleSpec(
        symbol="BTCUSDT",
        timeframe="4h",
        conditions=[
            Condition(left="rsi_14", op="<=", right=30),
            Condition(left="ema_21", op="<", right="c"),
        ],
    )
    snap = build_snapshot(spec, panel)
    assert snap["symbol"] == "BTCUSDT"
    assert snap["timeframe"] == "4h"
    assert "rsi_14" in snap["values"]
    assert len(snap["matched_conditions"]) == 2
