"""Test López de Prado metrics — PSR, DSR, PBO — against known-shape inputs."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.backtest.metrics import (
    compute_metrics,
    deflated_sharpe,
    probabilistic_sharpe,
    probability_of_overfit,
)


def test_psr_above_half_when_sharpe_positive() -> None:
    psr = probabilistic_sharpe(sharpe=1.5, n=252, sr_benchmark=0.0)
    assert 0.5 < psr <= 1.0


def test_psr_at_half_when_sharpe_equals_benchmark() -> None:
    psr = probabilistic_sharpe(sharpe=0.5, n=252, sr_benchmark=0.5)
    assert math.isclose(psr, 0.5, abs_tol=1e-6)


def test_dsr_more_pessimistic_than_psr_when_many_trials() -> None:
    """The whole point of DSR: as n_trials grows, the same Sharpe gets discounted."""
    psr = probabilistic_sharpe(sharpe=1.5, n=252)
    dsr_few = deflated_sharpe(sharpe=1.5, n=252, n_trials=1)
    dsr_many = deflated_sharpe(sharpe=1.5, n=252, n_trials=100)
    assert dsr_few == pytest.approx(psr)
    assert dsr_many < psr  # deflation kicks in
    assert 0.0 <= dsr_many <= 1.0


def test_pbo_zero_when_is_winner_always_top_oos() -> None:
    """In every fold the IS-best is also OOS-best (rank 1) → no overfit."""
    n_folds = 10
    pbo = probability_of_overfit([1] * n_folds, [1] * n_folds)
    assert pbo == 0.0


def test_pbo_one_when_is_winner_always_below_median() -> None:
    """In every fold the IS-best ranks last OOS → fully overfit."""
    n_folds = 10
    pbo = probability_of_overfit([1] * n_folds, [n_folds] * n_folds)
    assert pbo == 1.0


def test_compute_metrics_overfit_warning_for_losing_curve() -> None:
    """A noisy losing curve produces negative Sharpe → DSR < 0.5 → warning fires."""
    rng = np.random.default_rng(123)
    start = datetime(2025, 1, 1, tzinfo=UTC)
    # 100 days, mean drift -0.3%/day, daily std 1% (realistic crypto noise)
    rets = rng.normal(loc=-0.003, scale=0.01, size=100)
    equity_vals = 10_000.0 * np.cumprod(1.0 + rets)
    equity = [
        (start + timedelta(days=i), float(equity_vals[i])) for i in range(100)
    ]
    m = compute_metrics(
        equity_curve=equity, trades=[], initial_equity=10_000.0, n_trials=1
    )
    assert m.sharpe < 0
    assert m.deflated_sharpe < 0.5
    assert m.overfit_warning is True


def test_compute_metrics_includes_required_fields() -> None:
    """Smoke: returned schema covers what the agent and UI cite."""
    start = datetime(2025, 1, 1, tzinfo=UTC)
    equity = [(start + timedelta(days=i), 10_000.0 * (1 + 0.001 * i)) for i in range(100)]
    m = compute_metrics(
        equity_curve=equity, trades=[], initial_equity=10_000.0, n_trials=1
    )
    # Type assertions enforced by Pydantic, but verify the values are finite numbers.
    assert math.isfinite(m.sharpe)
    assert math.isfinite(m.deflated_sharpe)
    assert 0.0 <= m.probabilistic_sharpe <= 1.0
    assert 0.0 <= m.max_drawdown <= 1.0
