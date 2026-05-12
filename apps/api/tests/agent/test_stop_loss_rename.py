"""Pydantic model rename: `TradeIdea.invalidation` → `TradeIdea.stop_loss`.

After migration 008 + agent model update, the old field name is no longer a
valid input key. This test pins the rename so a future refactor that tries
to re-add the old name immediately fails.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agent.models import MarketRegime, Scenario, ToolCitation, TradeIdea


def _regime() -> MarketRegime:
    return MarketRegime(label="trending_up", citations=[])


def test_trade_idea_accepts_stop_loss_field() -> None:
    idea = TradeIdea(
        symbol="BTCUSDT",
        timeframe="1h",
        direction="long",
        regime=_regime(),
        entry=100.0,
        stop_loss=99.0,
        targets=[],
        risk_notes="placeholder",
        confidence="medium",
        summary_es="placeholder",
    )
    assert idea.stop_loss == 99.0


def test_trade_idea_rejects_legacy_invalidation_field() -> None:
    """`invalidation` is gone — Pydantic v2 with strict extras would reject.
    Our model has the default 'allow extra=ignore' so the value is silently
    dropped instead. We assert the silent drop: stop_loss stays None and
    'invalidation' does not become an attribute."""
    idea = TradeIdea.model_validate(
        {
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "direction": "long",
            "regime": {"label": "trending_up", "citations": []},
            "entry": 100.0,
            "invalidation": 99.0,  # old name — should be IGNORED, not adopted
            "targets": [],
            "risk_notes": "placeholder",
            "confidence": "medium",
            "summary_es": "placeholder",
        }
    )
    assert idea.stop_loss is None
    assert not hasattr(idea, "invalidation")


def test_scenario_stop_loss_field() -> None:
    s = Scenario(label="A", probability_pct=60, description="x", entry=100, stop_loss=99)
    assert s.stop_loss == 99
    # Old name silently dropped on Scenario too.
    s2 = Scenario.model_validate(
        {"label": "B", "probability_pct": 30, "description": "y", "invalidation": 99}
    )
    assert s2.stop_loss is None


def test_invalidation_condition_requires_citations() -> None:
    """Model-level: citations is min_length=1 — empty list rejected at parse."""
    from app.agent.models import InvalidationCondition

    with pytest.raises(ValidationError):
        InvalidationCondition.model_validate(
            {
                "spec": {
                    "symbol": "BTCUSDT",
                    "timeframe": "4h",
                    "conditions": [{"left": "c", "op": "<", "right": 60000}],
                },
                "rationale": "test",
                "citations": [],  # empty — must fail
            }
        )


def test_invalidation_condition_round_trips() -> None:
    from app.agent.models import InvalidationCondition

    cond = InvalidationCondition.model_validate(
        {
            "spec": {
                "symbol": "BTCUSDT",
                "timeframe": "4h",
                "conditions": [{"left": "c", "op": "<", "right": 60000}],
            },
            "rationale": "Range low break invalidates the bull thesis",
            "citations": [
                {"tool_name": "get_market_structure", "snapshot": {"level": 60000}}
            ],
        }
    )
    assert cond.spec.symbol == "BTCUSDT"
    assert cond.rationale.startswith("Range low")
    assert len(cond.citations) == 1
    assert cond.citations[0].tool_name == "get_market_structure"


def test_trade_idea_invalidation_conditions_cap_5() -> None:
    """max_length=5 on the list. 6 must fail at model parse."""

    def _cond(level: float) -> dict:
        return {
            "spec": {
                "symbol": "BTCUSDT",
                "timeframe": "4h",
                "conditions": [{"left": "c", "op": "<", "right": level}],
            },
            "rationale": "x",
            "citations": [{"tool_name": "get_market_structure", "snapshot": {}}],
        }

    with pytest.raises(ValidationError):
        TradeIdea.model_validate(
            {
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "direction": "long",
                "regime": {"label": "trending_up", "citations": []},
                "entry": 100.0,
                "stop_loss": 99.0,
                "targets": [],
                "risk_notes": "x",
                "confidence": "medium",
                "summary_es": "x",
                "invalidation_conditions": [_cond(60000 + i) for i in range(6)],
            }
        )
