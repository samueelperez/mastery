"""Rule-spec schema. Stored as jsonb in `alert_rules.spec`.

The shape mirrors `app.indicators.IndicatorSpec` so the runtime can feed the
declared indicators straight into `compute_panel` without translation. Conditions
reference output column names from the panel (e.g. `rsi_14`, `bb_lower`,
`macd_signal`) plus the canonical OHLCV columns (`o`, `h`, `l`, `c`, `v`).

Operators include `cross_above` / `cross_below` which compare the last TWO
closed bars; everything else is a single-row comparison on the latest closed bar.

Why a constrained schema instead of a free-text DSL: the agent emits this spec
directly as a tool argument; if the validator can reject malformed specs at
write-time, every running rule is by construction evaluable. No parser, no
runtime exceptions surfacing as missed alerts.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.indicators.panel import IndicatorSpec

Timeframe = Literal["15m", "1h", "4h", "1d"]
Operator = Literal["<", "<=", "==", ">=", ">", "cross_above", "cross_below"]
Logic = Literal["all", "any"]
RuleKind = Literal["candle_close"]

# Columns that always exist on the panel (the OHLCV input). Indicator outputs
# are validated dynamically in the evaluator since we'd need to mirror every
# alias here otherwise.
_OHLCV_COLUMNS: frozenset[str] = frozenset({"o", "h", "l", "c", "v"})


class Condition(BaseModel):
    """One predicate over the indicator panel.

    `left` is the column to read (`rsi_14`, `c`, `ema_21`, …).
    `right` is either a number or another column name; the evaluator decides at
    eval-time whether to look it up in the panel or treat it as a constant.
    """

    left: str = Field(..., min_length=1, max_length=64)
    op: Operator
    right: float | str

    @model_validator(mode="after")
    def _disallow_self_compare(self) -> Condition:
        if isinstance(self.right, str) and self.right == self.left:
            raise ValueError(f"Condition compares column {self.left!r} to itself")
        return self


class RuleSpec(BaseModel):
    """Full rule declaration. Persisted as jsonb; emitted by the agent verbatim."""

    kind: RuleKind = "candle_close"
    symbol: str = Field(..., min_length=1, max_length=32)
    timeframe: Timeframe
    indicators: list[IndicatorSpec] = Field(default_factory=list)
    conditions: list[Condition] = Field(..., min_length=1, max_length=10)
    logic: Logic = "all"

    @model_validator(mode="after")
    def _normalize_symbol(self) -> RuleSpec:
        # Symbols are stored uppercase to match Binance + the rest of the system.
        object.__setattr__(self, "symbol", self.symbol.upper())
        return self


def is_known_column(name: str) -> bool:
    """Coarse check: True for any OHLCV column. Indicator output names depend
    on the IndicatorSpec list and are checked at evaluation time."""
    return name in _OHLCV_COLUMNS
