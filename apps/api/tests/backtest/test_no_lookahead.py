"""Guard against look-ahead leakage in the simulator.

If the engine secretly used bar `i+1` to make decisions on bar `i`, removing
the future bars wouldn't change earlier behavior. Test: run on the first half
of the data and verify trades that close before the cutoff are byte-identical
to running on the full series.
"""

from __future__ import annotations

import polars as pl

# Force-import to populate the registry.
import app.backtest  # noqa: F401
from app.backtest.runner import _simulate
from app.backtest.strategies import get_strategy


def test_simulator_has_no_lookahead(trending_up_df: pl.DataFrame) -> None:
    strat = get_strategy("ema_cross_atr_stop")

    # Note: indicators (EMA/ATR) are computed in the strategy, which sees the
    # whole frame. To compare apples-to-apples, we slice the *signal frame*,
    # not the input frame — a real look-ahead leak would manifest in the
    # simulator using future entry/exit bools or stop_distances.
    full_sig = strat.fn(trending_up_df, strat.default_params)
    cutoff = len(trending_up_df) // 2

    sliced_sig = type(full_sig)(
        df=full_sig.df.slice(0, cutoff),
        entry=full_sig.entry.slice(0, cutoff),
        exit_=full_sig.exit_.slice(0, cutoff),
        stop_distance=(
            full_sig.stop_distance.slice(0, cutoff)
            if full_sig.stop_distance is not None else None
        ),
    )

    full_trades, _ = _simulate(
        full_sig, fees_bps=4.0, slippage_atr=0.05, initial_equity=10_000.0
    )
    sliced_trades, _ = _simulate(
        sliced_sig, fees_bps=4.0, slippage_atr=0.05, initial_equity=10_000.0
    )
    cutoff_ts = trending_up_df["ts"][cutoff - 1]

    # Every trade that fully closed before cutoff_ts in the full run must be
    # present and identical in the sliced run.
    full_closed_pre = [t for t in full_trades if t.exit_ts <= cutoff_ts]
    sliced_closed_pre = [t for t in sliced_trades if t.exit_ts <= cutoff_ts]

    assert len(full_closed_pre) == len(sliced_closed_pre), (
        "Different trade counts pre-cutoff implies look-ahead"
    )
    for f, s in zip(full_closed_pre, sliced_closed_pre, strict=True):
        assert f.entry_ts == s.entry_ts
        assert f.exit_ts == s.exit_ts
        assert f.entry_px == s.entry_px
        assert f.exit_px == s.exit_px
        assert f.r_multiple == s.r_multiple
