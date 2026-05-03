"""Strategy registry — strategies live as functions in this package.

A strategy reads an OHLCV `pl.DataFrame` (with at least ts/o/h/l/c/v columns)
and produces two boolean Polars expressions: when to enter and when to exit.
The engine in `app/backtest/runner.py` consumes these and simulates fills,
fees, and slippage.

Why functions and not classes? The agent calls strategies by `strategy_id`
through a tool; immutable functions make caching trivial and avoid hidden
state between backtest runs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import polars as pl


@dataclass(frozen=True)
class SignalFrame:
    """Output of a strategy's signal builder.

    `df` is the original OHLCV frame plus any indicator columns the strategy
    needed (these are kept so the runner can compute stops/targets and the
    UI can render them).

    `entry`, `exit`, and `stop_distance` are aligned with `df`:
      - entry[i] = True  → open a position at df['c'][i] (fillable from i+1's open in F4)
      - exit[i]  = True  → close any open position at df['c'][i]
      - stop_distance[i] = optional stop distance in price units (e.g. k * ATR);
                            None means "exit only on signal".
    """

    df: pl.DataFrame
    entry: pl.Series
    exit_: pl.Series
    stop_distance: pl.Series | None = None


# Each strategy declares its parameter schema as a Pydantic model OR a dict
# of {param_name: default_value}. Keep it simple — the agent reads this to
# know what knobs exist.
StrategyFn = Callable[[pl.DataFrame, dict[str, Any]], SignalFrame]


@dataclass(frozen=True)
class StrategyDef:
    id: str
    fn: StrategyFn
    description: str
    default_params: dict[str, Any] = field(default_factory=dict)


STRATEGY_REGISTRY: dict[str, StrategyDef] = {}


def register(
    id: str,
    *,
    description: str,
    default_params: dict[str, Any] | None = None,
) -> Callable[[StrategyFn], StrategyFn]:
    """Decorator: register a strategy function in the global registry."""

    def _decorator(fn: StrategyFn) -> StrategyFn:
        if id in STRATEGY_REGISTRY:
            raise ValueError(f"Strategy id {id!r} already registered")
        STRATEGY_REGISTRY[id] = StrategyDef(
            id=id, fn=fn, description=description, default_params=default_params or {}
        )
        return fn

    return _decorator


def get_strategy(id: str) -> StrategyDef:
    if id not in STRATEGY_REGISTRY:
        known = sorted(STRATEGY_REGISTRY.keys())
        raise KeyError(f"Strategy {id!r} not registered. Known: {known}")
    return STRATEGY_REGISTRY[id]
