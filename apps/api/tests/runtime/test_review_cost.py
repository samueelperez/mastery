"""Cost extraction del review_dispatcher — pure function, no agent involved.

`_extract_usage_and_cost` lee result.usage() y aplica la tabla de pricing
desde Settings. Validamos:
- Aritmética básica con valores conocidos.
- Cache read reduce el cost del input chargeable.
- Resultado None-safe si usage() lanza.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings
from app.reviewer.dispatcher import _extract_usage_and_cost


@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_write_input_tokens: int = 0


class _Result:
    def __init__(self, usage: _Usage) -> None:
        self._u = usage

    def usage(self) -> _Usage:
        return self._u


class _BrokenResult:
    def usage(self) -> Any:
        raise RuntimeError("no usage")


def test_cost_basic_no_cache() -> None:
    """3000 input + 1500 output @ default pricing
    (3.0 / 15.0 USD per 1M): 3000*3/1M + 1500*15/1M = 0.009 + 0.0225 = 0.0315"""
    result = _Result(_Usage(input_tokens=3000, output_tokens=1500))
    tokens, cost = _extract_usage_and_cost(result, get_settings())
    assert tokens is not None
    assert tokens["input"] == 3000
    assert tokens["output"] == 1500
    assert tokens["cache_read"] == 0
    assert cost is not None
    # Rounded to 6 decimals in implementation
    assert abs(cost - (3000 * 3.0 / 1_000_000 + 1500 * 15.0 / 1_000_000)) < 1e-6


def test_cost_with_cache_read() -> None:
    """Si 2400 de los 3000 input tokens son cache_read, solo 600 son charged at full rate."""
    result = _Result(
        _Usage(input_tokens=3000, output_tokens=1500, cache_read_input_tokens=2400)
    )
    tokens, cost = _extract_usage_and_cost(result, get_settings())
    assert tokens is not None
    assert tokens["cache_read"] == 2400
    expected = (
        600 * 3.0 / 1_000_000  # chargeable input
        + 2400 * 0.3 / 1_000_000  # cache reads at lower rate
        + 1500 * 15.0 / 1_000_000  # output
    )
    assert cost is not None
    assert abs(cost - expected) < 1e-6


def test_cost_handles_broken_usage() -> None:
    """If result.usage() raises, we return (None, None) — caller still
    persists the review, only telemetry suffers."""
    tokens, cost = _extract_usage_and_cost(_BrokenResult(), get_settings())
    assert tokens is None
    assert cost is None
