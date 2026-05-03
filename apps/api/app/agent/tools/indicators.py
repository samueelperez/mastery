"""get_indicators tool — compute one or more indicators on a symbol/timeframe panel."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from app.agent.deps import AgentDeps
from app.agent.tools._envelope import Provenance, ToolResult
from app.agent.tools._time import floor_to_timeframe, staleness_warning
from app.indicators import IndicatorSpec, compute_panel

# Indicators we expose group their derived columns under a clean key in `latest`
# so the agent doesn't have to know the column-naming convention internally.
_GROUPED_OUTPUTS: dict[str, list[str]] = {
    "macd": ["macd", "macd_signal", "macd_hist"],
    "bbands": ["bb_mid", "bb_upper", "bb_lower", "bb_bw"],
    "adx": ["adx", "plus_di", "minus_di"],
    "vwap": ["vwap"],
}


class IndicatorPanel(BaseModel):
    """5-row tail per indicator + a `latest` snapshot for synthesis.

    The agent can read `latest` for direct claims (e.g. "RSI is 67") and
    `series_tail` for direction (e.g. "RSI rising from 55 to 67").
    """

    asof: datetime
    series_tail: dict[str, list[float | None]] = Field(default_factory=dict)
    latest: dict[str, Any] = Field(default_factory=dict)


def _column_for_spec(spec: IndicatorSpec) -> str:
    """Map an IndicatorSpec to its primary column name."""
    if spec.name in _GROUPED_OUTPUTS:
        return _GROUPED_OUTPUTS[spec.name][0]
    length = spec.length or 14
    return f"{spec.name}_{length}"


def _safe_float(v: Any) -> float | None:
    """Convert a Polars value to a JSON-safe float, or None for null/NaN."""
    if v is None:
        return None
    f = float(v)
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def register_indicator_tools(agent: Agent[AgentDeps, object]) -> None:
    @agent.tool
    async def get_indicators(
        ctx: RunContext[AgentDeps],
        symbol: str,
        timeframe: Literal["15m", "1h", "4h", "1d"],
        indicators: Annotated[
            list[IndicatorSpec],
            Field(min_length=1, max_length=10),
        ],
        lookback: Annotated[int, Field(ge=50, le=1500)] = 300,
    ) -> ToolResult[IndicatorPanel]:
        """Compute one or more indicators on a CLOSED-candle window.

        Default lengths if omitted: ema=21, sma=20, rsi=14, atr=14, bbands=20,
        adx=14. macd uses 12/26/9. vwap uses session anchor.
        """
        symbol = symbol.upper()
        cutoff = floor_to_timeframe(datetime.now(tz=UTC), timeframe)

        async with ctx.deps.session_factory() as session:
            df = await compute_panel(
                session,
                exchange=ctx.deps.exchange,
                symbol=symbol,
                timeframe=timeframe,
                lookback=lookback,
                specs=indicators,
                until=cutoff,
            )

        if df.height == 0:
            return ToolResult(
                data=IndicatorPanel(asof=cutoff),
                provenance=Provenance(
                    source=f"db.ohlcv:{ctx.deps.exchange}:{symbol}:{timeframe}",
                    as_of=cutoff,
                    rows=0,
                    warnings=["no candles in lookback window"],
                ),
            )

        # Build series_tail: last 5 values of each indicator column
        last_rows = df.tail(5)
        latest_row = df.tail(1).to_dicts()[0]
        series_tail: dict[str, list[float | None]] = {}
        latest: dict[str, Any] = {}

        for spec in indicators:
            cols = _GROUPED_OUTPUTS.get(spec.name)
            if cols:
                # Grouped: include each sub-column individually
                for col in cols:
                    series_tail[col] = [_safe_float(v) for v in last_rows[col].to_list()]
                latest[spec.name] = {
                    col: _safe_float(latest_row.get(col)) for col in cols
                }
            else:
                col = _column_for_spec(spec)
                series_tail[col] = [_safe_float(v) for v in last_rows[col].to_list()]
                latest[col] = _safe_float(latest_row.get(col))

        last_ts = latest_row["ts"]
        warnings = []
        if w := staleness_warning(last_closed=last_ts, timeframe=timeframe):
            warnings.append(w)

        ctx.deps.log.info(
            "tool.get_indicators",
            symbol=symbol,
            timeframe=timeframe,
            n_indicators=len(indicators),
            rows=df.height,
        )

        return ToolResult(
            data=IndicatorPanel(asof=last_ts, series_tail=series_tail, latest=latest),
            provenance=Provenance(
                source=f"db.ohlcv:{ctx.deps.exchange}:{symbol}:{timeframe}",
                as_of=last_ts,
                rows=df.height,
                warnings=warnings,
            ),
        )
