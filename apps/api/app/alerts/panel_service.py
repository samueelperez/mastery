"""Shared panel-builder for rule evaluation.

Extracted from `app.alerts.runtime` so both AlertsRuntime AND SetupRuntime can
build the same enriched OHLCV+indicator panel for a (symbol, timeframe). The
two consumers have different keyspaces (alert_rules vs journal_trades) so no
cache is shared here; the function is pure given the SqlAlchemy session.

The union+lookback rules are bias-tuned to the existing alert engine — see
`_max_lookback` for the Wilder-smoothing rationale.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

import polars as pl
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import floor_to_timeframe
from app.alerts.dsl import RuleSpec
from app.core.exchanges.binance_adapter import EXCHANGE_NAME
from app.market.indicators import IndicatorSpec, compute_panel


def _max_lookback(specs: Iterable[IndicatorSpec]) -> int:
    """The panel needs max(spec.length) × 3 rows to warm up indicators (Wilder
    smoothing on RSI/ATR/ADX needs ~2× length, plus headroom for cross_*
    operators reading the previous bar). Floor at 60 so a 1-rule RSI(14) tick
    fetches ~60 candles, not 300."""
    lengths = [s.length or 50 for s in specs]
    return max(60, max(lengths, default=50) * 3)


def _union_specs(specs_lists: list[list[IndicatorSpec]]) -> list[IndicatorSpec]:
    """Deduplicate IndicatorSpec across rules (so we compute each indicator
    once even if 5 rules want RSI(14))."""
    seen: dict[tuple[str, int | None, str], IndicatorSpec] = {}
    for specs in specs_lists:
        for s in specs:
            seen.setdefault((s.name, s.length, s.source), s)
    return list(seen.values())


async def compute_panel_for_specs(
    session: AsyncSession,
    *,
    symbol: str,
    timeframe: str,
    specs: list[RuleSpec],
    until: datetime | None = None,
) -> pl.DataFrame:
    """Compute the enriched panel once for a set of RuleSpecs sharing
    (symbol, timeframe).

    Caller is responsible for grouping specs by (symbol, timeframe) before
    invoking — we don't validate that here. Returns an empty DataFrame if
    no specs are supplied (defensive).
    """
    if not specs:
        return pl.DataFrame()
    union = _union_specs([s.indicators for s in specs])
    if until is None:
        until = floor_to_timeframe(datetime.now(tz=UTC), timeframe)
    return await compute_panel(
        session,
        exchange=EXCHANGE_NAME,
        symbol=symbol,
        timeframe=timeframe,
        lookback=_max_lookback(union),
        specs=union,
        until=until,
    )
