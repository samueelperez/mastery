"""Polars-vectorized backtest engine.

Fast enough for sweep-style research without leaving the Polars layer. Models
fees (basis points off each fill) and slippage (fraction of the candle's ATR
added against the trade direction). Long-only in F2; shorts come in F2.5+.

Output is intentionally rich: a list of `Trade` objects, an equity curve, and
a `StrategyMetrics` snapshot — all serializable to the `backtest_runs` row.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import polars as pl
import structlog
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools._time import floor_to_timeframe
from app.backtest.metrics import StrategyMetrics, compute_metrics
from app.backtest.strategies import SignalFrame, get_strategy
from app.storage.ohlcv_repo import fetch_range

log = structlog.get_logger(__name__)


class BacktestSpec(BaseModel):
    """Inputs needed to reproduce a backtest exactly. Persisted alongside the result."""

    strategy_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    symbol: str
    timeframe: Literal["15m", "1h", "4h", "1d"]
    since: datetime
    until: datetime | None = None
    fees_bps: float = Field(default=4.0, ge=0)            # 0.04% taker (Binance USDT-M default)
    slippage_atr: float = Field(default=0.05, ge=0)       # 5% of ATR added against trade
    initial_equity: float = Field(default=10_000.0, gt=0)
    seed: int | None = None


class Trade(BaseModel):
    entry_ts: datetime
    exit_ts: datetime
    side: Literal["long"]
    entry_px: float
    exit_px: float
    r_multiple: float
    pnl: float
    bars_held: int
    exit_reason: Literal["signal", "stop"]


class BacktestResult(BaseModel):
    run_id: str
    spec: BacktestSpec
    metrics: StrategyMetrics
    trades: list[Trade]
    equity_curve: list[tuple[datetime, float]]


# -----------------------------------------------------------------------------
# Engine
# -----------------------------------------------------------------------------


@dataclass
class _OpenPosition:
    entry_ts: datetime
    entry_px: float
    stop_px: float | None
    bars_held: int = 0


def _simulate(
    sigframe: SignalFrame,
    *,
    fees_bps: float,
    slippage_atr: float,
    initial_equity: float,
) -> tuple[list[Trade], list[tuple[datetime, float]]]:
    """Walk the candles in order; long-only; one position at a time.

    Fees: bps applied to BOTH entry and exit notional.
    Slippage: `slippage_atr * stop_distance` is added to entry_px and subtracted
              from exit_px (we always cross the spread against ourselves).
    """
    df = sigframe.df
    ts = df["ts"].to_list()
    closes = df["c"].to_list()
    lows = df["l"].to_list()
    entry_arr = sigframe.entry.to_list()
    exit_arr = sigframe.exit_.to_list()
    stop_dist_arr = (
        sigframe.stop_distance.to_list() if sigframe.stop_distance is not None
        else [None] * len(closes)
    )

    fees_frac = fees_bps / 10_000.0
    trades: list[Trade] = []
    equity = initial_equity
    equity_curve: list[tuple[datetime, float]] = []
    open_pos: _OpenPosition | None = None

    for i in range(len(closes)):
        # mark-to-market: equity at this bar reflects open position's unrealized PnL
        if open_pos is not None:
            unrealized = (closes[i] - open_pos.entry_px) / open_pos.entry_px * initial_equity
            equity_curve.append((ts[i], initial_equity + unrealized))
        else:
            equity_curve.append((ts[i], equity))

        if open_pos is None:
            if entry_arr[i]:
                # apply slippage on entry
                slip = (stop_dist_arr[i] or 0.0) * slippage_atr
                entry_px = closes[i] + slip
                stop_px = (
                    entry_px - (stop_dist_arr[i] or 0.0)
                    if stop_dist_arr[i] is not None
                    else None
                )
                # apply fees on entry notional
                equity -= initial_equity * fees_frac
                open_pos = _OpenPosition(entry_ts=ts[i], entry_px=entry_px, stop_px=stop_px)
        else:
            open_pos.bars_held += 1
            exit_reason: Literal["signal", "stop"] | None = None

            # stop check first: if low pierces the stop, fill at stop_px
            if open_pos.stop_px is not None and lows[i] <= open_pos.stop_px:
                fill_px = open_pos.stop_px
                exit_reason = "stop"
            elif exit_arr[i]:
                slip = (stop_dist_arr[i] or 0.0) * slippage_atr
                fill_px = closes[i] - slip
                exit_reason = "signal"

            if exit_reason is not None:
                # apply fees on exit notional
                equity -= initial_equity * fees_frac
                ret = (fill_px - open_pos.entry_px) / open_pos.entry_px
                pnl = initial_equity * ret
                equity += pnl
                # R-multiple: pnl / risk-per-trade. Risk approximated as
                # initial_equity * |entry - stop| / entry, falling back to 1.
                if open_pos.stop_px is not None and open_pos.stop_px < open_pos.entry_px:
                    risk_frac = (open_pos.entry_px - open_pos.stop_px) / open_pos.entry_px
                    risk = initial_equity * risk_frac
                    r_mult = pnl / risk if risk > 0 else 0.0
                else:
                    r_mult = ret * 100.0  # no stop → use raw return × 100 as proxy

                trades.append(
                    Trade(
                        entry_ts=open_pos.entry_ts,
                        exit_ts=ts[i],
                        side="long",
                        entry_px=open_pos.entry_px,
                        exit_px=fill_px,
                        r_multiple=round(r_mult, 4),
                        pnl=round(pnl, 4),
                        bars_held=open_pos.bars_held,
                        exit_reason=exit_reason,
                    )
                )
                open_pos = None

    return trades, equity_curve


# -----------------------------------------------------------------------------
# Public entrypoint — also persists to backtest_runs
# -----------------------------------------------------------------------------


async def run_backtest(
    session: AsyncSession,
    *,
    spec: BacktestSpec,
    exchange: str = "binance_usdm",
    persist: bool = True,
) -> BacktestResult:
    """Fetch OHLCV, run the strategy, simulate fills, persist + return result."""
    strat = get_strategy(spec.strategy_id)

    until = spec.until or floor_to_timeframe(datetime.now(tz=UTC), spec.timeframe)
    rows = await fetch_range(
        session,
        exchange=exchange,
        symbol=spec.symbol.upper(),
        timeframe=spec.timeframe,
        since=spec.since,
        until=until,
        limit=10_000,
    )
    if len(rows) < 50:
        raise ValueError(
            f"Insufficient data: {len(rows)} candles for {spec.symbol} {spec.timeframe} "
            f"from {spec.since} to {until}. Need ≥50 for a meaningful backtest."
        )

    df = pl.DataFrame(
        {
            "ts": [r.ts for r in rows],
            "o": [r.o for r in rows],
            "h": [r.h for r in rows],
            "l": [r.l for r in rows],
            "c": [r.c for r in rows],
            "v": [r.v for r in rows],
        }
    )

    params = {**strat.default_params, **spec.params}
    sigframe = strat.fn(df, params)
    trades, equity = _simulate(
        sigframe,
        fees_bps=spec.fees_bps,
        slippage_atr=spec.slippage_atr,
        initial_equity=spec.initial_equity,
    )

    metrics = compute_metrics(
        equity_curve=equity, trades=[t.model_dump() for t in trades],
        initial_equity=spec.initial_equity, n_trials=1,
    )

    run_id = str(uuid.uuid4())
    if persist:
        await session.execute(
            text(
                """
                INSERT INTO backtest_runs (
                    id, strategy_id, params, symbol, timeframe,
                    range_start, range_end, fees_bps, slippage_atr, seed,
                    status, metrics, equity_curve, finished_at
                ) VALUES (
                    CAST(:id AS uuid), :sid, CAST(:params AS jsonb), :sym, :tf,
                    :rs, :re, :fees, :slip, :seed,
                    'done', CAST(:metrics AS jsonb), CAST(:equity AS jsonb), now()
                )
                """
            ),
            {
                "id": run_id,
                "sid": spec.strategy_id,
                "params": json.dumps(params),
                "sym": spec.symbol.upper(),
                "tf": spec.timeframe,
                "rs": spec.since,
                "re": until,
                "fees": spec.fees_bps,
                "slip": spec.slippage_atr,
                "seed": spec.seed,
                "metrics": metrics.model_dump_json(),
                "equity": json.dumps(
                    [(t.isoformat(), round(e, 4)) for t, e in equity]
                ),
            },
        )
        # Update strategy_metrics aggregate
        await session.execute(
            text(
                """
                INSERT INTO strategy_metrics (strategy_id, last_run_id, n_runs, best_dsr, best_pbo)
                VALUES (:sid, CAST(:rid AS uuid), 1, :dsr, NULL)
                ON CONFLICT (strategy_id) DO UPDATE SET
                  last_run_id = EXCLUDED.last_run_id,
                  n_runs = strategy_metrics.n_runs + 1,
                  best_dsr = GREATEST(COALESCE(strategy_metrics.best_dsr, -1e9), EXCLUDED.best_dsr),
                  last_updated = now()
                """
            ),
            {"sid": spec.strategy_id, "rid": run_id, "dsr": metrics.deflated_sharpe},
        )

    log.info(
        "backtest.run",
        run_id=run_id,
        strategy=spec.strategy_id,
        n_trades=len(trades),
        sharpe=metrics.sharpe,
        dsr=metrics.deflated_sharpe,
        max_dd=metrics.max_drawdown,
    )
    return BacktestResult(
        run_id=run_id, spec=spec, metrics=metrics, trades=trades, equity_curve=equity
    )
