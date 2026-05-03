"""Backtesting engine + métricas (F2 Chunk 2).

Polars-vectorized engine for sweep-friendly research; skfolio for
CombinatorialPurgedCV; own implementation of Bailey & López de Prado
Deflated/Probabilistic Sharpe + PBO in `metrics.py`.

Strategies live in `strategies/` and are registered into a global
`STRATEGY_REGISTRY` so the agent can call them by id.
"""

import app.backtest.strategies.bollinger_reversion

# Importing the strategy modules registers them via decorators on import.
import app.backtest.strategies.ema_cross  # noqa: F401
from app.backtest.cpcv import run_cpcv
from app.backtest.metrics import (
    StrategyMetrics,
    compute_metrics,
    deflated_sharpe,
    probabilistic_sharpe,
    probability_of_overfit,
)
from app.backtest.runner import BacktestResult, BacktestSpec, run_backtest
from app.backtest.strategies import STRATEGY_REGISTRY, StrategyDef, register
from app.backtest.walk_forward import run_walk_forward

__all__ = [
    "STRATEGY_REGISTRY",
    "BacktestResult",
    "BacktestSpec",
    "StrategyDef",
    "StrategyMetrics",
    "compute_metrics",
    "deflated_sharpe",
    "probabilistic_sharpe",
    "probability_of_overfit",
    "register",
    "run_backtest",
    "run_cpcv",
    "run_walk_forward",
]
