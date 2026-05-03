"""Common ToolResult envelope — every agent tool wraps its data in this.

Provenance turns the LLM's downstream citations into something auditable: every
field (`source`, `as_of`, `rows`, `warnings`) reflects an actual fact about the
DB query, not the model's belief.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Provenance(BaseModel):
    """Where the data came from, when, and any caveats."""

    source: str = Field(
        ..., description="e.g. 'db.ohlcv:binance_usdm:BTCUSDT:1h'"
    )
    as_of: datetime = Field(
        ..., description="ts of the newest closed candle used in the response"
    )
    rows: int = Field(..., ge=0)
    warnings: list[str] = Field(
        default_factory=list,
        description="e.g. 'stale: last close 4h35m ago vs 1h tf'",
    )


class ToolResult[T](BaseModel):
    data: T
    provenance: Provenance
