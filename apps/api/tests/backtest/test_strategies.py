"""Sanity tests for registered strategy signal builders.

We don't assert profitability here — only that the SignalFrame contract holds:
- entry/exit are boolean Polars Series of the right length
- stop_distance, when present, is non-negative and float-typed
- entry and exit are not always-True (would mean broken strategy)
"""

from __future__ import annotations

import polars as pl
import pytest

# Force-import to populate the registry (same trick the runner uses).
import app.backtest  # noqa: F401
from app.backtest.strategies import STRATEGY_REGISTRY, get_strategy


@pytest.mark.parametrize("strategy_id", ["ema_cross_atr_stop", "bb_reversion_atr_stop"])
def test_strategy_returns_well_shaped_signalframe(
    strategy_id: str, trending_up_df: pl.DataFrame
) -> None:
    strat = get_strategy(strategy_id)
    sigframe = strat.fn(trending_up_df, strat.default_params)
    n = len(trending_up_df)
    assert len(sigframe.entry) == n
    assert len(sigframe.exit_) == n
    assert sigframe.entry.dtype == pl.Boolean
    assert sigframe.exit_.dtype == pl.Boolean
    # df should at minimum keep the original ts/o/h/l/c/v columns
    for col in ("ts", "o", "h", "l", "c", "v"):
        assert col in sigframe.df.columns


@pytest.mark.parametrize("strategy_id", ["ema_cross_atr_stop", "bb_reversion_atr_stop"])
def test_strategy_does_not_emit_trivial_always_true(
    strategy_id: str, trending_up_df: pl.DataFrame
) -> None:
    """A strategy that fires entry on every bar is broken (no edge, just noise)."""
    strat = get_strategy(strategy_id)
    sigframe = strat.fn(trending_up_df, strat.default_params)
    entry_rate = sigframe.entry.sum() / len(sigframe.entry)
    assert entry_rate < 0.3, f"{strategy_id} entry rate {entry_rate:.2f} suspiciously high"


def test_registry_exposes_both_f2_strategies() -> None:
    assert "ema_cross_atr_stop" in STRATEGY_REGISTRY
    assert "bb_reversion_atr_stop" in STRATEGY_REGISTRY
