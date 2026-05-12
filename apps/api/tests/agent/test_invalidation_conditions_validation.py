"""Validator extension for invalidation_conditions + expires_at.

The validator is registered via `@agent.output_validator` and reads from
`ctx.messages` (which Pydantic-AI manages). Testing the full path with a
real Agent.run() requires an OpenRouter key + network.

Instead, this file pins the LIGHTWEIGHT model-level invariants that
Pydantic enforces BEFORE the validator runs (`min_length`, `max_length`,
required-when-set). That's where ~half of the rules live; the rest
(tool-name correlation, expires_at_future, BTCUSDT cross-symbol gate)
are validated in production by `validators.py::must_cite_quantitative_claims`
and exercised in the end-to-end smoke test.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agent.models import InvalidationCondition, ToolCitation, TradeIdea


def _base_idea_dict() -> dict:
    return {
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
    }


def _cond_dict(*, citations: list[dict] | None = None) -> dict:
    return {
        "spec": {
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "conditions": [{"left": "c", "op": "<", "right": 60000}],
        },
        "rationale": "Range low break invalidates the thesis",
        "citations": (
            citations
            if citations is not None
            else [{"tool_name": "get_market_structure", "snapshot": {"level": 60000}}]
        ),
    }


def test_invalidation_condition_rejects_empty_citations() -> None:
    with pytest.raises(ValidationError):
        InvalidationCondition.model_validate(_cond_dict(citations=[]))


def test_invalidation_condition_rejects_blank_rationale() -> None:
    bad = _cond_dict()
    bad["rationale"] = ""
    with pytest.raises(ValidationError):
        InvalidationCondition.model_validate(bad)


def test_invalidation_condition_accepts_valid_minimal() -> None:
    cond = InvalidationCondition.model_validate(_cond_dict())
    assert cond.spec.symbol == "BTCUSDT"
    assert cond.spec.timeframe == "4h"
    assert len(cond.spec.conditions) == 1


def test_trade_idea_invalidation_conditions_default_empty() -> None:
    idea = TradeIdea.model_validate(_base_idea_dict())
    assert idea.invalidation_conditions == []
    assert idea.expires_at is None
    assert idea.expires_at_rationale is None
    assert idea.expires_at_citations == []


def test_trade_idea_with_one_condition_round_trips() -> None:
    base = _base_idea_dict()
    base["invalidation_conditions"] = [_cond_dict()]
    idea = TradeIdea.model_validate(base)
    assert len(idea.invalidation_conditions) == 1
    cond = idea.invalidation_conditions[0]
    assert cond.spec.symbol == "BTCUSDT"
    assert cond.citations[0].tool_name == "get_market_structure"


def test_invalidation_conditions_capped_at_five() -> None:
    base = _base_idea_dict()
    base["invalidation_conditions"] = [_cond_dict() for _ in range(6)]
    with pytest.raises(ValidationError):
        TradeIdea.model_validate(base)


def test_invalidation_conditions_five_is_ok() -> None:
    base = _base_idea_dict()
    base["invalidation_conditions"] = [_cond_dict() for _ in range(5)]
    idea = TradeIdea.model_validate(base)
    assert len(idea.invalidation_conditions) == 5


def test_expires_at_accepts_iso_string() -> None:
    base = _base_idea_dict()
    base["expires_at"] = "2030-01-01T00:00:00Z"
    base["expires_at_rationale"] = "funding squeeze unwinds in 24h"
    base["expires_at_citations"] = [
        {"tool_name": "get_funding_rate", "snapshot": {"cumul_7d": 0.4}}
    ]
    idea = TradeIdea.model_validate(base)
    assert idea.expires_at is not None
    assert idea.expires_at_rationale.startswith("funding")


def test_tool_citation_minimum_tool_name() -> None:
    # tool_name must exist; an empty cite list dict can't satisfy that.
    with pytest.raises(ValidationError):
        ToolCitation.model_validate({})
