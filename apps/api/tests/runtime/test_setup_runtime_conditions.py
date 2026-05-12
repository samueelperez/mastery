"""Pure-function tests for `setup_runtime._parse_conditions` and the
underlying alerts evaluator coupling.

The full SetupRuntime market loop opens DB sessions and computes panels —
those are exercised in the end-to-end verification (manual psql + chat).
Here we cover the pure parsing branch + the OR-combine logic, both of
which are pure given the dict and a polars panel.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl

from app.alerts.dsl import Condition, RuleSpec
from app.alerts.evaluator import evaluate_rule
from app.setups.repo import OpenSetupRow
from app.setups.runtime import _parse_conditions


def _setup(invalidation_conditions: list[dict]) -> OpenSetupRow:
    return OpenSetupRow(
        id="00000000-0000-0000-0000-000000000001",
        user_id="u",
        symbol="BTCUSDT",
        timeframe="1h",
        side="long",
        status="pending",
        entry_px=64000.0,
        stop_loss_px=63500.0,
        targets=[],
        invalidation_conditions=invalidation_conditions,
        expires_at=None,
        proposed_at=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        entry_hit_at=None,
    )


def _panel(rows: list[dict[str, float]]) -> pl.DataFrame:
    base = datetime(2026, 5, 4, 0, 0, tzinfo=UTC)
    data: dict[str, list] = {"ts": [base + timedelta(hours=i) for i in range(len(rows))]}
    keys = {k for r in rows for k in r}
    for k in keys:
        data[k] = [float(r.get(k, float("nan"))) for r in rows]
    return pl.DataFrame(data)


def test_parse_conditions_filters_invalid_shapes() -> None:
    """Garbage spec entries are dropped silently — the agent validator
    already guarantees shape, but the runtime is defensive."""
    setup = _setup(
        invalidation_conditions=[
            {
                "spec": {
                    "symbol": "BTCUSDT",
                    "timeframe": "4h",
                    "conditions": [{"left": "c", "op": "<", "right": 60000}],
                },
                "rationale": "ok",
                "citations": [{"tool_name": "get_market_structure", "snapshot": {}}],
            },
            {"spec": "not a dict", "rationale": "broken", "citations": []},
            # Missing spec key entirely.
            {"rationale": "broken", "citations": []},
        ]
    )
    specs = _parse_conditions(setup)
    assert len(specs) == 1
    assert specs[0].symbol == "BTCUSDT"


def test_parse_conditions_empty_list() -> None:
    assert _parse_conditions(_setup([])) == []


def test_close_below_level_fires() -> None:
    spec = RuleSpec(
        symbol="BTCUSDT",
        timeframe="4h",
        conditions=[Condition(left="c", op="<", right=60000)],
    )
    panel = _panel([{"c": 59800.0}])
    assert evaluate_rule(spec, panel) is True


def test_close_above_level_does_not_fire() -> None:
    spec = RuleSpec(
        symbol="BTCUSDT",
        timeframe="4h",
        conditions=[Condition(left="c", op="<", right=60000)],
    )
    panel = _panel([{"c": 60500.0}])
    assert evaluate_rule(spec, panel) is False


def test_or_semantics_across_multiple_conditions_in_one_spec() -> None:
    """AND/OR within a single RuleSpec is `logic`. The setup-runtime layer
    treats the LIST of RuleSpecs as OR-combined; here we pin the within-
    spec `any` path that the agent uses to express 'A OR B' inside one
    condition record."""
    spec = RuleSpec(
        symbol="BTCUSDT",
        timeframe="4h",
        conditions=[
            Condition(left="c", op="<", right=60000),
            Condition(left="rsi_14", op=">", right=80),
        ],
        logic="any",
    )
    # Only RSI fires.
    assert evaluate_rule(spec, _panel([{"c": 61000, "rsi_14": 82}])) is True
    # Only close fires.
    assert evaluate_rule(spec, _panel([{"c": 59800, "rsi_14": 65}])) is True
    # Neither.
    assert evaluate_rule(spec, _panel([{"c": 61000, "rsi_14": 70}])) is False
