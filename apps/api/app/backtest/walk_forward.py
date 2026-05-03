"""Walk-forward analysis — anchored vs rolling, with optional embargo.

Splits the time range into N consecutive (in-sample, out-of-sample) windows.
For each split: re-instantiate the strategy with the SAME params (no
re-optimization in F2; that's the agent's job once per strategy) and measure
out-of-sample performance only.

The point of walk-forward in F2 is to detect when a strategy's edge is
front-loaded — strong in 2024 but flat in 2025. The CPCV in `cpcv.py` covers
the broader question of "is this Sharpe statistically distinguishable from
the best of N random tries".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.metrics import StrategyMetrics
from app.backtest.runner import BacktestSpec, run_backtest

log = structlog.get_logger(__name__)


@dataclass
class WalkForwardFold:
    fold: int
    in_sample_start: datetime
    in_sample_end: datetime
    out_sample_start: datetime
    out_sample_end: datetime
    metrics: StrategyMetrics
    n_trades: int


@dataclass
class WalkForwardResult:
    folds: list[WalkForwardFold]
    aggregate_oos_metrics: StrategyMetrics


async def run_walk_forward(
    session: AsyncSession,
    *,
    base_spec: BacktestSpec,
    is_months: int = 12,
    oos_months: int = 3,
    embargo_days: int = 1,
    exchange: str = "binance_usdm",
) -> WalkForwardResult:
    """Roll forward N=auto folds across [base_spec.since, base_spec.until]."""
    end = base_spec.until or datetime.now(tz=base_spec.since.tzinfo)
    is_delta = timedelta(days=is_months * 30)
    oos_delta = timedelta(days=oos_months * 30)
    embargo = timedelta(days=embargo_days)

    folds: list[WalkForwardFold] = []
    fold_idx = 0
    is_start = base_spec.since
    while True:
        is_end = is_start + is_delta
        oos_start = is_end + embargo
        oos_end = oos_start + oos_delta
        if oos_end > end:
            break

        # Run only on the OOS window — this is the honest measurement.
        oos_spec = BacktestSpec(
            **{**base_spec.model_dump(), "since": oos_start, "until": oos_end}
        )
        try:
            result = await run_backtest(
                session, spec=oos_spec, exchange=exchange, persist=False
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

        folds.append(
            WalkForwardFold(
                fold=fold_idx,
                in_sample_start=is_start,
                in_sample_end=is_end,
                out_sample_start=oos_start,
                out_sample_end=oos_end,
                metrics=result.metrics,
                n_trades=len(result.trades),
            )
        )
        fold_idx += 1
        is_start = is_start + oos_delta  # roll, not anchored

    # Aggregate OOS: stitch equity curves end-to-end, recompute
    if not folds:
        agg = StrategyMetrics(
            n_trades=0, win_rate=0, avg_win_R=0, avg_loss_R=0, expectancy_R=0,
            sharpe=0, sortino=0, max_drawdown=0, max_drawdown_duration_bars=0,
            calmar=0, mar=0, ulcer_index=0, tail_ratio=0, skew=0, kurtosis=0,
            probabilistic_sharpe=0.5, deflated_sharpe=0, overfit_warning=True,
        )
    else:
        # Average per-fold metrics (simple aggregator; UI can show per-fold detail)
        n_total = sum(f.n_trades for f in folds)
        avg_sharpe = sum(f.metrics.sharpe for f in folds) / len(folds)
        avg_dsr = sum(f.metrics.deflated_sharpe for f in folds) / len(folds)
        worst_dd = max(f.metrics.max_drawdown for f in folds)
        agg = StrategyMetrics(
            n_trades=n_total,
            win_rate=sum(f.metrics.win_rate * f.n_trades for f in folds) / max(n_total, 1),
            avg_win_R=0.0, avg_loss_R=0.0,
            expectancy_R=sum(f.metrics.expectancy_R * f.n_trades for f in folds) / max(n_total, 1),
            sharpe=round(avg_sharpe, 4),
            sortino=0.0,
            max_drawdown=round(worst_dd, 4),
            max_drawdown_duration_bars=max((f.metrics.max_drawdown_duration_bars for f in folds), default=0),
            calmar=0.0, mar=0.0, ulcer_index=0.0, tail_ratio=0.0, skew=0.0, kurtosis=0.0,
            probabilistic_sharpe=0.5,
            deflated_sharpe=round(avg_dsr, 4),
            overfit_warning=avg_dsr < 0.5,
        )

    log.info("walk_forward.done", n_folds=len(folds), avg_dsr=agg.deflated_sharpe)
    return WalkForwardResult(folds=folds, aggregate_oos_metrics=agg)
