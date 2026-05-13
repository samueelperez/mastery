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
from app.backtest.metrics import (
    StrategyMetrics,
    annualization_factor_for,
    compute_metrics,
)
from app.backtest.strategies import SignalFrame, get_strategy
from app.market.ohlcv.repo import fetch_range

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
    # Notional con que abrió la posición — compounding real: cada trade
    # dimensiona sobre el equity vigente al momento del fill, no sobre el
    # initial_equity. Esto hace que la curva sea geométrica/multiplicativa
    # (la convención correcta para CAGR/Calmar/MAR) en lugar de aritmética.
    notional: float
    bars_held: int = 0


@dataclass
class _PendingSignal:
    """A signal captured at the close of bar i, awaiting fill at the open of bar i+1.

    `slip_dist` snapshots the stop_distance (≈ ATR × k) at signal-time; we use it
    to size slippage rather than the next bar's ATR, since slippage is paid
    crossing the spread at fill, not from future volatility.
    """

    fired: bool = False
    slip_dist: float | None = None


def _simulate(
    sigframe: SignalFrame,
    *,
    fees_bps: float,
    slippage_atr: float,
    initial_equity: float,
) -> tuple[list[Trade], list[tuple[datetime, float]]]:
    """Walk the candles in order; long-only; one position at a time.

    **Execution convention** (commit ec9ba57's runner had close-to-close fill,
    which silently took look-ahead by deciding AND filling on the same close).
    The honest convention, paired with what F4 paper trading will do:

      - At the close of bar `i` the strategy emits entry / exit / stop_distance.
      - We CARRY that signal as a pending instruction.
      - On the OPEN of bar `i+1` we fill: entry_px = opens[i+1] + slip,
        exit_px = opens[i+1] - slip. Stop loss is the exception — it triggers
        intra-bar at stop_px when `low <= stop_px`.

    Fees: bps applied to BOTH entry and exit notional.
    Slippage: `slip = stop_distance × slippage_atr`. Same formula as before, but
    `stop_distance` is locked at signal-time, not fill-time.
    Last-bar trailing signals are dropped (no next bar to fill on).
    """
    df = sigframe.df
    ts = df["ts"].to_list()
    opens = df["o"].to_list()
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
    pending_entry = _PendingSignal()
    pending_exit = _PendingSignal()

    for i in range(len(closes)):
        # 1. Fill any entry signal pending from bar i-1 at this bar's open.
        if open_pos is None and pending_entry.fired:
            sd = pending_entry.slip_dist
            slip = (sd or 0.0) * slippage_atr
            entry_px = opens[i] + slip
            stop_px = entry_px - sd if sd is not None and sd > 0 else None
            # Notional = equity vigente (compounding real). Fees sobre ese notional.
            notional = equity
            equity -= notional * fees_frac
            open_pos = _OpenPosition(
                entry_ts=ts[i], entry_px=entry_px, stop_px=stop_px, notional=notional
            )
            pending_entry = _PendingSignal()  # consumed

        # 2. Mark-to-market for the equity curve. Usamos el notional con que
        # abrimos la posición (compounding real): la curva refleja el equity
        # actual = realizado + unrealized del trade abierto.
        if open_pos is not None:
            unrealized = (
                (closes[i] - open_pos.entry_px) / open_pos.entry_px * open_pos.notional
            )
            equity_curve.append((ts[i], equity + unrealized))
        else:
            equity_curve.append((ts[i], equity))

        # 3. Process exits on this bar — stop loss intra-bar, OR pending exit at open.
        if open_pos is not None:
            open_pos.bars_held += 1
            exit_reason: Literal["signal", "stop"] | None = None
            fill_px: float = 0.0

            if open_pos.stop_px is not None and lows[i] <= open_pos.stop_px:
                # Intra-bar stop fill at stop_px (no slippage modelled past the stop;
                # binance USDM stops fill at stop trigger price for stop-market orders).
                fill_px = open_pos.stop_px
                exit_reason = "stop"
            elif pending_exit.fired:
                sd = pending_exit.slip_dist
                slip = (sd or 0.0) * slippage_atr
                fill_px = opens[i] - slip
                exit_reason = "signal"
            pending_exit = _PendingSignal()  # whether triggered or staled by stop, drop

            if exit_reason is not None:
                # Fees y P&L sobre el notional con que abrió, no sobre
                # initial_equity. Esto compone la curva multiplicativamente:
                # un buy-and-hold sobre price.x2 → equity.x2.
                equity -= open_pos.notional * fees_frac
                ret = (fill_px - open_pos.entry_px) / open_pos.entry_px
                pnl = open_pos.notional * ret
                equity += pnl
                if open_pos.stop_px is not None and open_pos.stop_px < open_pos.entry_px:
                    risk_frac = (open_pos.entry_px - open_pos.stop_px) / open_pos.entry_px
                    risk = open_pos.notional * risk_frac
                    r_mult = pnl / risk if risk > 0 else 0.0
                else:
                    # Sin stop, R-multiple no se puede definir. Devolvemos
                    # 0.0 como marker en lugar del proxy "ret × 100" anterior
                    # (que contaminaba expectancy_R con valores no-R).
                    r_mult = 0.0

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

        # 4. Capture this bar's signal at close for the NEXT bar's open fill.
        if open_pos is None and entry_arr[i]:
            pending_entry = _PendingSignal(fired=True, slip_dist=stop_dist_arr[i])
        elif open_pos is not None and exit_arr[i]:
            pending_exit = _PendingSignal(fired=True, slip_dist=stop_dist_arr[i])

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

    # n_trials para DSR: # de runs PREVIOS de esta estrategia + 1 (este run).
    # Esto deflata el Sharpe contra el trial-bias acumulado — cada run nuevo
    # paga la "penalización" de Bonferroni-Bailey-LdP por el conjunto de
    # configuraciones probadas hasta el momento. Sin esto, el `best_dsr`
    # agregado se infla porque cada run individual asume n_trials=1.
    # Auditoría 2026-05 #B6.
    if persist:
        n_trials_row = await session.execute(
            text(
                "SELECT COALESCE(n_runs, 0) FROM strategy_metrics "
                "WHERE strategy_id = :sid"
            ),
            {"sid": spec.strategy_id},
        )
        prev_runs = n_trials_row.scalar() or 0
        n_trials_for_dsr = prev_runs + 1
    else:
        n_trials_for_dsr = 1

    metrics = compute_metrics(
        equity_curve=equity, trades=[t.model_dump() for t in trades],
        initial_equity=spec.initial_equity, n_trials=n_trials_for_dsr,
        annualization_factor=annualization_factor_for(spec.timeframe),
    )

    run_id = str(uuid.uuid4())
    if persist:
        await session.execute(
            text(
                """
                INSERT INTO backtest_runs (
                    id, strategy_id, params, symbol, timeframe,
                    range_start, range_end, fees_bps, slippage_atr, seed,
                    status, metrics, equity_curve, trades, finished_at
                ) VALUES (
                    CAST(:id AS uuid), :sid, CAST(:params AS jsonb), :sym, :tf,
                    :rs, :re, :fees, :slip, :seed,
                    'done', CAST(:metrics AS jsonb), CAST(:equity AS jsonb),
                    CAST(:trades AS jsonb), now()
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
                "trades": json.dumps(
                    [t.model_dump(mode="json") for t in trades]
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
