"""Read-only REST endpoints for the /research surface.

GET /backtests — list runs (filterable by strategy/symbol/timeframe).
GET /backtests/{id} — full detail with equity_curve.

The agent writes through `run_backtest`; the UI reads here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_dependency

router = APIRouter()


class BacktestRunSummary(BaseModel):
    """One row from the list view — no equity_curve to keep it lightweight."""

    id: str
    strategy_id: str
    symbol: str
    timeframe: str
    range_start: datetime
    range_end: datetime
    fees_bps: float
    slippage_atr: float
    status: str
    created_at: datetime
    finished_at: datetime | None = None
    metrics: dict[str, Any] | None = None  # metrics JSONB, parsed


class BacktestRunDetail(BacktestRunSummary):
    """List row + params + equity_curve for the drilldown view."""

    params: dict[str, Any] = Field(default_factory=dict)
    equity_curve: list[tuple[str, float]] = Field(default_factory=list)


@router.get("/backtests", response_model=list[BacktestRunSummary], tags=["research"])
async def list_backtests(
    session: Annotated[AsyncSession, Depends(session_dependency)],
    strategy_id: Annotated[str | None, Query()] = None,
    symbol: Annotated[str | None, Query()] = None,
    timeframe: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[BacktestRunSummary]:
    rows = (
        await session.execute(
            text(
                """
                SELECT id::text, strategy_id, symbol, timeframe,
                       range_start, range_end, fees_bps, slippage_atr,
                       status, created_at, finished_at, metrics
                FROM backtest_runs
                WHERE (CAST(:sid AS text) IS NULL OR strategy_id = :sid)
                  AND (CAST(:sym AS text) IS NULL OR symbol = :sym)
                  AND (CAST(:tf  AS text) IS NULL OR timeframe = :tf)
                ORDER BY created_at DESC
                LIMIT :lim
                """
            ),
            {
                "sid": strategy_id,
                "sym": symbol.upper() if symbol else None,
                "tf": timeframe,
                "lim": limit,
            },
        )
    ).mappings().all()
    return [BacktestRunSummary(**dict(r)) for r in rows]


@router.get("/backtests/{run_id}", response_model=BacktestRunDetail, tags=["research"])
async def get_backtest(
    run_id: str,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> BacktestRunDetail:
    row = (
        await session.execute(
            text(
                """
                SELECT id::text, strategy_id, symbol, timeframe, params,
                       range_start, range_end, fees_bps, slippage_atr,
                       status, created_at, finished_at, metrics, equity_curve
                FROM backtest_runs
                WHERE id = CAST(:rid AS uuid)
                """
            ),
            {"rid": run_id},
        )
    ).mappings().one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail=f"backtest_run {run_id} not found")
    return BacktestRunDetail(**dict(row))
