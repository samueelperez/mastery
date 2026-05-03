"""Test helpers for indicator tests.

Two strategies:
1. **Synthetic frames** built from a simple closes / OHLC list — used to verify
   indicators against pure-Python reference implementations.
2. **DB-backed slice** of real BTCUSDT 1h candles (skip if DB unavailable) — used
   for sanity checks against real-world data without pinning ground-truth values
   that drift across exchanges.
"""

from __future__ import annotations

import math
import os
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest


def make_lf(
    closes: Iterable[float],
    *,
    highs: Iterable[float] | None = None,
    lows: Iterable[float] | None = None,
    opens: Iterable[float] | None = None,
    volumes: Iterable[float] | None = None,
    start: datetime | None = None,
    interval: timedelta = timedelta(hours=1),
) -> pl.LazyFrame:
    """Build a LazyFrame of synthetic OHLCV candles.

    Defaults: highs = closes + 0.5, lows = closes - 0.5, opens = closes,
    volumes = 1.0 for each row. Useful for indicators that need OHLC.
    """
    closes = list(closes)
    n = len(closes)
    if highs is None:
        highs = [c + 0.5 for c in closes]
    if lows is None:
        lows = [c - 0.5 for c in closes]
    if opens is None:
        opens = list(closes)
    if volumes is None:
        volumes = [1.0] * n
    if start is None:
        start = datetime(2025, 1, 1, tzinfo=UTC)
    ts = [start + interval * i for i in range(n)]
    return pl.LazyFrame(
        {
            "ts": ts,
            "o": list(opens),
            "h": list(highs),
            "l": list(lows),
            "c": closes,
            "v": list(volumes),
        }
    )


# -----------------------------------------------------------------------------
# Pure-Python reference implementations — kept side-by-side with Polars so a bug
# in either is loud. Don't import from `app.indicators` here.
# -----------------------------------------------------------------------------


def py_sma(values: list[float], length: int) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(values)):
        if i + 1 < length:
            out.append(None)
        else:
            out.append(sum(values[i - length + 1 : i + 1]) / length)
    return out


def py_ewm(values: list[float], *, alpha: float, min_samples: int) -> list[float | None]:
    """EMA with adjust=False: seed = first value, then α·x + (1-α)·prev.

    With `min_samples` we mask the first `min_samples-1` outputs as None so the
    polars and pure-python views match — even though the recursion is well-defined
    from index 0.
    """
    out: list[float | None] = []
    prev: float | None = None
    for i, x in enumerate(values):
        if x is None or math.isnan(x):
            out.append(None)
            continue
        prev = x if prev is None else alpha * x + (1 - alpha) * prev
        out.append(prev if i + 1 >= min_samples else None)
    return out


def py_ema(values: list[float], length: int) -> list[float | None]:
    return py_ewm(values, alpha=2 / (length + 1), min_samples=length)


def py_rsi(values: list[float], length: int = 14) -> list[float | None]:
    """Wilder RSI: EWM with α=1/length on positive/negative price diffs."""
    diff = [None] + [values[i] - values[i - 1] for i in range(1, len(values))]
    gain = [0.0 if (d is None or d <= 0) else d for d in diff]
    loss = [0.0 if (d is None or d >= 0) else -d for d in diff]
    avg_gain = py_ewm(gain, alpha=1 / length, min_samples=length)
    avg_loss = py_ewm(loss, alpha=1 / length, min_samples=length)
    out: list[float | None] = []
    for g, l_ in zip(avg_gain, avg_loss, strict=False):
        if g is None or l_ is None:
            out.append(None)
        elif l_ == 0:
            out.append(100.0)
        else:
            rs = g / l_
            out.append(100.0 - 100.0 / (1.0 + rs))
    return out


def py_atr(
    h: list[float], l_: list[float], c: list[float], length: int = 14
) -> list[float | None]:
    n = len(c)
    tr: list[float | None] = []
    for i in range(n):
        if i == 0:
            tr.append(h[i] - l_[i])
        else:
            prev_c = c[i - 1]
            tr.append(max(h[i] - l_[i], abs(h[i] - prev_c), abs(l_[i] - prev_c)))
    return py_ewm([t or 0.0 for t in tr], alpha=1 / length, min_samples=length)


# -----------------------------------------------------------------------------
# Optional DB-backed fixture
# -----------------------------------------------------------------------------


def _has_db() -> bool:
    return bool(os.environ.get("DATABASE_URL"))


@pytest.fixture(scope="session")
def has_db() -> bool:
    return _has_db()


@pytest.fixture
def synthetic_closes() -> list[float]:
    return [10.0, 10.5, 11.0, 10.8, 10.6, 11.2, 11.5, 12.0, 11.7, 11.9,
            12.3, 12.6, 12.4, 12.8, 13.0, 13.2, 13.5, 13.1, 12.9, 13.3,
            13.7, 14.0, 13.8, 14.2, 14.5, 14.3, 14.1, 14.6, 14.9, 15.2]


@pytest.fixture
def synthetic_ohlc() -> dict[str, list[float]]:
    closes = [10.0, 10.5, 11.0, 10.8, 10.6, 11.2, 11.5, 12.0, 11.7, 11.9,
              12.3, 12.6, 12.4, 12.8, 13.0, 13.2, 13.5, 13.1, 12.9, 13.3,
              13.7, 14.0, 13.8, 14.2, 14.5, 14.3, 14.1, 14.6, 14.9, 15.2]
    return {
        "c": closes,
        "h": [c + 0.4 for c in closes],
        "l": [c - 0.3 for c in closes],
        "o": closes,
        "v": [100.0 + i for i in range(len(closes))],
    }
