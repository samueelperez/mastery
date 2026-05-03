"""Pydantic schemas the agent's structured output must conform to.

These are the contract between the LLM and the validator: every quantitative
claim (entry, invalidation, target prices) MUST carry citations to tool calls
that produced the underlying data. The validator at `app.agent.validators`
enforces this — the prompt only describes it.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Timeframe = Literal["15m", "1h", "4h", "1d"]
Direction = Literal["long", "short", "no_trade"]
Bias = Literal["bull", "bear", "range"]
Confidence = Literal["low", "medium", "high"]
RegimeLabel = Literal["trending_up", "trending_down", "ranging", "volatile_expansion"]


class ToolCitation(BaseModel):
    """Pointer to the tool call whose output backs a numeric claim.

    `tool_call_id` must match an actual tool_call_id from the run trace; the
    validator rejects fabricated IDs. `snapshot` is the relevant excerpt of the
    tool's output (the actual number) so the UI can render it without re-fetching.
    """

    tool_call_id: str = Field(..., description="ID from the run trace; validator checks this.")
    tool_name: str
    snapshot: dict[str, Any] = Field(
        default_factory=dict,
        description="Excerpt of the tool output: the value being cited (e.g. {'ema_55': 67234.1}).",
    )


class Confluence(BaseModel):
    timeframe: Timeframe
    bias: Bias
    reasons: list[str] = Field(..., min_length=1)
    citations: list[ToolCitation] = Field(default_factory=list)


class MarketRegime(BaseModel):
    label: RegimeLabel
    citations: list[ToolCitation] = Field(default_factory=list)


class TradeIdeaTarget(BaseModel):
    label: str = Field(..., description="e.g. 'TP1', 'TP2', 'TP_runner'")
    price: float
    rationale: str
    citations: list[ToolCitation] = Field(default_factory=list)


class TradeIdea(BaseModel):
    """The agent's structured analysis output.

    Use direction='no_trade' when conditions don't justify a setup; the validator
    permits empty entry/invalidation/targets/confluences in that case.
    """

    symbol: str
    timeframe: Timeframe
    direction: Direction
    regime: MarketRegime
    confluences: list[Confluence] = Field(default_factory=list)

    entry: float | None = None
    entry_rationale: str | None = None
    entry_citations: list[ToolCitation] = Field(default_factory=list)

    invalidation: float | None = Field(default=None, description="Stop loss; logical, not %.")
    invalidation_rationale: str | None = None
    invalidation_citations: list[ToolCitation] = Field(default_factory=list)

    targets: list[TradeIdeaTarget] = Field(default_factory=list)

    risk_notes: str = Field(
        ...,
        description="Slippage, funding, leverage caveats. Required for every idea.",
    )
    confidence: Confidence
    summary_es: str = Field(
        ...,
        description="2-3 sentence Spanish summary used as the card header.",
        max_length=500,
    )
