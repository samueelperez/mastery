"""Walk-forward analysis — anchored vs rolling, with optional embargo + purge.

Splits the time range into N consecutive (in-sample, out-of-sample) windows.
For each split: re-instantiate the strategy with the SAME params (no
re-optimization in F2; that's the agent's job once per strategy) and measure
out-of-sample performance only.

WARM-UP PURGE (Sprint D, 2026-05): cada fold OOS extiende su fetch hacia
atrás `warmup_bars` velas para que indicadores con lookback largo (EMA200,
SMA200, ATR, etc.) ya estén estables al entrar al OOS. Los trades cuya
`entry_ts < oos_start` se PURGAN — son del periodo de warm-up y no
representan performance OOS real. Sin esto, los primeros ~200 bars de cada
fold producían 0 señales (indicadores en NaN) y el fold subestimaba la
edge real de la estrategia.

El point de walk-forward en F2 es detectar cuándo el edge de una estrategia
está front-loaded — fuerte en 2024 pero plano en 2025. CPCV en `cpcv.py`
cubre la pregunta más amplia de "¿este Sharpe es distinguible del mejor
de N tries aleatorios?".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.metrics import (
    StrategyMetrics,
    annualization_factor_for,
    compute_metrics,
)
from app.backtest.runner import BacktestSpec, Trade, run_backtest

log = structlog.get_logger(__name__)


_TF_MINUTES: dict[str, int] = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}


def _warmup_delta(timeframe: str, bars: int) -> timedelta:
    """`bars` velas del timeframe traducidas a timedelta para warm-up de indicadores."""
    minutes = _TF_MINUTES.get(timeframe, 60)
    return timedelta(minutes=minutes * bars)


@dataclass
class WalkForwardFold:
    fold: int
    in_sample_start: datetime
    in_sample_end: datetime
    out_sample_start: datetime
    out_sample_end: datetime
    metrics: StrategyMetrics
    n_trades: int
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)


@dataclass
class WalkForwardResult:
    folds: list[WalkForwardFold]
    aggregate_oos_metrics: StrategyMetrics


async def run_walk_forward(
    session: AsyncSession,
    *,
    base_spec: BacktestSpec,
    user_id: str,
    is_months: int = 12,
    oos_months: int = 3,
    embargo_days: int = 1,
    warmup_bars: int = 200,
    exchange: str = "binance_usdm",
) -> WalkForwardResult:
    """Roll forward N=auto folds across [base_spec.since, base_spec.until].

    `warmup_bars` velas se cargan ANTES de cada `oos_start` para que los
    indicadores arranquen estables; los trades abiertos en ese prefijo se
    purgan de las métricas OOS.
    """
    end = base_spec.until or datetime.now(tz=base_spec.since.tzinfo)
    is_delta = timedelta(days=is_months * 30)
    oos_delta = timedelta(days=oos_months * 30)
    embargo = timedelta(days=embargo_days)
    warmup = _warmup_delta(base_spec.timeframe, warmup_bars)

    folds: list[WalkForwardFold] = []
    fold_idx = 0
    is_start = base_spec.since
    while True:
        is_end = is_start + is_delta
        oos_start = is_end + embargo
        oos_end = oos_start + oos_delta
        if oos_end > end:
            break

        # Fetch con prefijo de warm-up; las métricas se calculan sólo sobre
        # los trades cuya entry_ts >= oos_start (purga de warm-up).
        fetch_since = oos_start - warmup
        oos_spec = BacktestSpec(
            **{**base_spec.model_dump(), "since": fetch_since, "until": oos_end}
        )
        try:
            result = await run_backtest(
                session, spec=oos_spec, user_id=user_id, exchange=exchange, persist=False
            )
        except ValueError as e:
            # Insufficient data in this OOS window; skip.
            log.warning(
                "walk_forward.fold_skip",
                fold=fold_idx,
                start=oos_start.isoformat(),
                end=oos_end.isoformat(),
                error=str(e),
            )
            is_start = is_start + oos_delta
            fold_idx += 1
            continue

        # Purga de warm-up: trades anteriores a oos_start se descartan, y la
        # equity curve se reslizea + renormaliza para que arranque en el
        # initial_equity al inicio del OOS real.
        purged_trades = [t for t in result.trades if t.entry_ts >= oos_start]
        oos_curve = [(ts, eq) for (ts, eq) in result.equity_curve if ts >= oos_start]
        if oos_curve:
            base_eq = oos_curve[0][1]
            scale = base_spec.initial_equity / base_eq if base_eq > 0 else 1.0
            oos_curve = [(ts, eq * scale) for ts, eq in oos_curve]

        fold_metrics = compute_metrics(
            equity_curve=oos_curve,
            trades=[t.model_dump() for t in purged_trades],
            initial_equity=base_spec.initial_equity,
            n_trials=1,  # un solo OOS por fold; el trial-bias es fold-level
            annualization_factor=annualization_factor_for(base_spec.timeframe),
        )

        folds.append(
            WalkForwardFold(
                fold=fold_idx,
                in_sample_start=is_start,
                in_sample_end=is_end,
                out_sample_start=oos_start,
                out_sample_end=oos_end,
                metrics=fold_metrics,
                n_trades=len(purged_trades),
                equity_curve=oos_curve,
                trades=purged_trades,
            )
        )
        fold_idx += 1
        is_start = is_start + oos_delta  # roll, not anchored

    # Aggregate OOS: stitch each fold's equity curve into one continuous series
    # (each fold restarts at initial_equity, so we rescale forward) then run
    # `compute_metrics` once on the stitched curve. This gives REAL DSR / PSR /
    # skew / kurt / max_dd over the union of OOS — not per-fold averages, which
    # is incoherent for non-linear stats like DSR.
    if not folds:
        agg = StrategyMetrics(
            n_trades=0, win_rate=0, avg_win_R=0, avg_loss_R=0, expectancy_R=0,
            sharpe=0, sortino=0, max_drawdown=0, max_drawdown_duration_bars=0,
            calmar=0, mar=0, ulcer_index=0, tail_ratio=0, skew=0, kurtosis=0,
            probabilistic_sharpe=0.5, deflated_sharpe=0, overfit_warning=True,
        )
    else:
        stitched_curve: list[tuple[datetime, float]] = []
        running = base_spec.initial_equity
        for f in folds:
            fold_curve = f.equity_curve
            if not fold_curve:
                continue
            fold_initial = fold_curve[0][1]
            if fold_initial <= 0:
                continue
            for ts_, eq in fold_curve:
                stitched_curve.append((ts_, running * eq / fold_initial))
            running = stitched_curve[-1][1]

        all_trades = [t.model_dump() for f in folds for t in f.trades]
        agg = compute_metrics(
            equity_curve=stitched_curve,
            trades=all_trades,
            initial_equity=base_spec.initial_equity,
            n_trials=len(folds),  # number of OOS folds we actually evaluated
            annualization_factor=annualization_factor_for(base_spec.timeframe),
        )

    log.info(
        "walk_forward.done",
        n_folds=len(folds),
        agg_dsr=agg.deflated_sharpe,
        agg_sharpe=agg.sharpe,
    )
    return WalkForwardResult(folds=folds, aggregate_oos_metrics=agg)
