"""Persistence for B.2 paper fills.

`insert_paper_fill` writes one row per simulated fill (entry or exit).
The repo is intentionally minimal: capture-then-aggregate. The calibration
job is `aggregate_observed_slippage_p75`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class PaperFillRow:
    """Inputs to `insert_paper_fill` — what we persist after each simulated fill."""

    trade_id: str
    user_id: str
    symbol: str
    timeframe: str
    side: Literal["long", "short"]
    kind: Literal["entry", "exit"]
    intended_px: float
    filled_px: float
    qty_pct: float
    slippage_bps: float
    fee_bps: float
    funding_bps: float
    filled_at: datetime
    spread_pct: float | None = None
    atr_pct: float | None = None
    metadata: dict[str, object] | None = None


async def insert_paper_fill(session: AsyncSession, row: PaperFillRow) -> str:
    """Persist a paper_fill row and return the generated id."""
    result = await session.execute(
        text(
            """
            INSERT INTO paper_fills (
                trade_id, user_id, symbol, timeframe, side, kind,
                intended_px, filled_px, qty_pct,
                spread_pct, atr_pct,
                slippage_bps, fee_bps, funding_bps,
                filled_at, metadata
            ) VALUES (
                CAST(:trade_id AS uuid), :user_id, :symbol, :timeframe,
                :side, :kind,
                :intended_px, :filled_px, :qty_pct,
                :spread_pct, :atr_pct,
                :slippage_bps, :fee_bps, :funding_bps,
                :filled_at, CAST(:metadata AS jsonb)
            )
            RETURNING id::text
            """
        ),
        {
            "trade_id": row.trade_id,
            "user_id": row.user_id,
            "symbol": row.symbol.upper(),
            "timeframe": row.timeframe,
            "side": row.side,
            "kind": row.kind,
            "intended_px": row.intended_px,
            "filled_px": row.filled_px,
            "qty_pct": row.qty_pct,
            "spread_pct": row.spread_pct,
            "atr_pct": row.atr_pct,
            "slippage_bps": row.slippage_bps,
            "fee_bps": row.fee_bps,
            "funding_bps": row.funding_bps,
            "filled_at": row.filled_at,
            "metadata": json.dumps(row.metadata or {}),
        },
    )
    return str(result.scalar_one())


async def aggregate_observed_slippage_p75(
    session: AsyncSession,
    *,
    symbol: str,
    lookback_days: int = 90,
) -> dict[str, float] | None:
    """Returns a calibration snapshot for the symbol over the last
    `lookback_days`: p75 slippage_bps, mean atr_pct, sample count.

    Returns None if fewer than 20 fills are available — not enough to trust
    the percentile. Caller uses the ratio `p75_slippage_bps / (mean_atr_pct
    * 100)` to derive the new `SLIPPAGE_BUFFER_R[symbol]` (units of R).
    """
    row = (
        await session.execute(
            text(
                """
                SELECT
                    PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY slippage_bps)
                        AS p75_slippage_bps,
                    AVG(atr_pct) AS mean_atr_pct,
                    COUNT(*) AS n_fills
                FROM paper_fills
                WHERE symbol = :sym
                  AND filled_at >= now() - make_interval(days => :days)
                """
            ),
            {"sym": symbol.upper(), "days": lookback_days},
        )
    ).mappings().one_or_none()
    if row is None:
        return None
    n = int(row["n_fills"] or 0)
    if n < 20:
        return None
    return {
        "p75_slippage_bps": float(row["p75_slippage_bps"] or 0.0),
        "mean_atr_pct": float(row["mean_atr_pct"] or 0.0),
        "n_fills": float(n),
    }
