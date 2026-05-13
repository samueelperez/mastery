"""Unit tests for ``app.agent.cost.extract_usage_and_cost``.

Pinned because the helper is the single source of truth for "how many
USD did this run cost?" — getting the cache-read accounting wrong would
quietly bias every cost dashboard in the system.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "sk-test-placeholder")

from app.agent.cost import extract_usage_and_cost
from app.core.config import get_settings


def _result_with(**usage_kwargs: int) -> SimpleNamespace:
    """Build a stand-in for ``AgentRunResult`` whose ``.usage()`` returns
    a duck-typed object carrying the requested counters."""
    usage_obj = SimpleNamespace(**usage_kwargs)
    return SimpleNamespace(usage=lambda: usage_obj)


def test_returns_none_when_usage_method_raises():
    class _Broken:
        def usage(self):
            raise RuntimeError("provider returned no usage")

    tokens, cost = extract_usage_and_cost(_Broken(), get_settings())
    assert tokens is None
    assert cost is None


def test_extracts_canonical_anthropic_field_names():
    result = _result_with(
        input_tokens=1_000,
        output_tokens=500,
        cache_read_input_tokens=200,
        cache_write_input_tokens=0,
    )
    tokens, cost = extract_usage_and_cost(result, get_settings())
    assert tokens == {
        "input": 1_000,
        "output": 500,
        "cache_read": 200,
        "cache_create": 0,
        "total": 1_500,
    }
    assert cost is not None and cost > 0


def test_falls_back_to_openrouter_legacy_field_names():
    """Older pydantic-ai releases (and some OpenRouter responses) use
    ``request_tokens`` / ``response_tokens`` instead of input/output."""
    result = _result_with(request_tokens=2_000, response_tokens=800)
    tokens, _cost = extract_usage_and_cost(result, get_settings())
    assert tokens is not None
    assert tokens["input"] == 2_000
    assert tokens["output"] == 800
    assert tokens["total"] == 2_800


def test_cache_read_charged_at_discount_rate():
    """Cache-read tokens use ``review_price_cache_read_per_m_usd`` (the
    cheaper rate). The same input token count with vs without cache_read
    must yield a strictly lower cost when cache_read > 0."""
    settings = get_settings()
    no_cache = _result_with(input_tokens=10_000, output_tokens=0, cache_read_input_tokens=0)
    with_cache = _result_with(
        input_tokens=10_000, output_tokens=0, cache_read_input_tokens=8_000
    )
    _, cost_no_cache = extract_usage_and_cost(no_cache, settings)
    _, cost_with_cache = extract_usage_and_cost(with_cache, settings)
    assert cost_no_cache is not None and cost_with_cache is not None
    assert cost_with_cache < cost_no_cache


def test_cost_is_rounded_to_six_decimals():
    """The persisted ``cost_usd`` column is ``numeric(10, 6)`` — sub-microcent
    precision wastes bytes and produces ugly histograms."""
    result = _result_with(input_tokens=1, output_tokens=1)
    _, cost = extract_usage_and_cost(result, get_settings())
    assert cost is not None
    assert cost == pytest.approx(round(cost, 6))
