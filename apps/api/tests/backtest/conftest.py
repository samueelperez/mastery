"""Shared synthetic OHLCV fixtures for backtest tests.

We avoid hitting the DB by feeding hand-built Polars frames straight into the
strategy signal builders and `_simulate`. Two regimes:

- `trending_up_df`: clean uptrend with one mid-period pullback. EMA cross
  should produce ≥1 trade with positive R.
- `mean_reverting_df`: oscillating around a level. Bollinger reversion should
  produce multiple trades.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest


def _ohlcv_from_close(close: np.ndarray, *, start: datetime, tf_seconds: int = 14_400) -> pl.DataFrame:
    """Build a Polars OHLCV frame from a close-price series.

    Open = previous close (or close[0] for first bar). High/low get a small
    spread so ATR is non-zero.
    """
    n = close.size
    ts = [start + timedelta(seconds=tf_seconds * i) for i in range(n)]
    open_ = np.concatenate(([close[0]], close[:-1]))
    spread = np.maximum(np.abs(close - open_) * 0.5, close * 0.001)
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    vol = np.full(n, 100.0)
    return pl.DataFrame(
        {
            "ts": ts,
            "o": open_.astype(np.float64),
            "h": high.astype(np.float64),
            "l": low.astype(np.float64),
            "c": close.astype(np.float64),
            "v": vol,
        }
    )


@pytest.fixture
def trending_up_df() -> pl.DataFrame:
    """500 bars of monotonic uptrend with one ~10% mid-period pullback."""
    rng = np.random.default_rng(42)
    base = np.linspace(100.0, 200.0, 500)
    noise = rng.normal(0, 0.5, 500)
    base[200:230] *= 0.9  # pullback to trigger stops
    return _ohlcv_from_close(
        base + noise, start=datetime(2025, 1, 1, tzinfo=UTC)
    )


@pytest.fixture
def mean_reverting_df() -> pl.DataFrame:
    """500 bars oscillating ±15% around 100."""
    rng = np.random.default_rng(7)
    t = np.arange(500)
    base = 100.0 + 15.0 * np.sin(t / 20.0) + rng.normal(0, 1.5, 500)
    return _ohlcv_from_close(base, start=datetime(2025, 1, 1, tzinfo=UTC))
