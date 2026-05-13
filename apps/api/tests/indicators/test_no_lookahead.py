"""Critical anti-pattern test: indicators must not look at future data.

For a true streaming indicator, computing on a prefix of length n must produce
the same value at index `n-1` as computing on the full series. This catches:
- accidental use of `adjust=True` in EMA (uses all preceding values' weights)
- shifts in the wrong direction
- vectorised expressions that scan beyond the current row

We test this for every indicator on a longish synthetic series.
"""

from __future__ import annotations

import math
import random

import pytest

from app.market.indicators.core import atr, ema, rsi, sma
from app.market.indicators.momentum import bbands, macd
from app.market.indicators.trend import adx
from app.market.indicators.volume import vwap
from tests.indicators.conftest import make_lf

random.seed(20260503)
_LEN = 200
_CLOSES = [100.0 + math.sin(i / 5) * 5 + random.gauss(0, 0.5) for i in range(_LEN)]
_HIGHS = [c + abs(random.gauss(0, 0.4)) for c in _CLOSES]
_LOWS = [c - abs(random.gauss(0, 0.4)) for c in _CLOSES]
_VOLS = [10.0 + random.uniform(-2, 2) for _ in range(_LEN)]


def _make(n: int):
    return make_lf(_CLOSES[:n], highs=_HIGHS[:n], lows=_LOWS[:n], volumes=_VOLS[:n])


def _check_prefix(col: str, indicator_fn, *, n: int, k: int, tol: float) -> None:
    full = indicator_fn(_make(n + k)).collect()[col].to_list()
    prefix = indicator_fn(_make(n)).collect()[col].to_list()
    assert full[n - 1] == pytest.approx(prefix[n - 1], abs=tol, nan_ok=True), (
        f"{col}: full[{n - 1}]={full[n - 1]} vs prefix[{n - 1}]={prefix[n - 1]}"
    )


@pytest.mark.parametrize("n,k", [(60, 5), (90, 30), (150, 10)])
def test_sma_no_lookahead(n: int, k: int) -> None:
    _check_prefix("sma_20", lambda lf: sma(lf, length=20), n=n, k=k, tol=1e-9)


@pytest.mark.parametrize("n,k", [(60, 5), (90, 30), (150, 10)])
def test_ema_no_lookahead(n: int, k: int) -> None:
    _check_prefix("ema_21", lambda lf: ema(lf, length=21), n=n, k=k, tol=1e-9)


@pytest.mark.parametrize("n,k", [(60, 5), (90, 30), (150, 10)])
def test_rsi_no_lookahead(n: int, k: int) -> None:
    _check_prefix("rsi_14", lambda lf: rsi(lf, length=14), n=n, k=k, tol=1e-7)


@pytest.mark.parametrize("n,k", [(60, 5), (90, 30), (150, 10)])
def test_atr_no_lookahead(n: int, k: int) -> None:
    _check_prefix("atr_14", lambda lf: atr(lf, length=14), n=n, k=k, tol=1e-7)


@pytest.mark.parametrize("n,k", [(60, 5), (90, 30), (150, 10)])
def test_macd_no_lookahead(n: int, k: int) -> None:
    for col in ("macd", "macd_signal", "macd_hist"):
        _check_prefix(col, macd, n=n, k=k, tol=1e-7)


@pytest.mark.parametrize("n,k", [(60, 5), (90, 30), (150, 10)])
def test_bbands_no_lookahead(n: int, k: int) -> None:
    for col in ("bb_mid", "bb_upper", "bb_lower", "bb_bw"):
        _check_prefix(col, lambda lf: bbands(lf, length=20), n=n, k=k, tol=1e-7)


@pytest.mark.parametrize("n,k", [(60, 5), (90, 30), (150, 10)])
def test_adx_no_lookahead(n: int, k: int) -> None:
    for col in ("adx", "plus_di", "minus_di"):
        _check_prefix(col, lambda lf: adx(lf, length=14), n=n, k=k, tol=1e-7)


@pytest.mark.parametrize("n,k", [(60, 5), (90, 30), (150, 10)])
def test_vwap_no_lookahead_within_session(n: int, k: int) -> None:
    """VWAP resets at session boundary, so the no-lookahead invariant holds
    only WITHIN a session. Our synthetic series is hourly starting at midnight,
    so all 200 candles span ~8 days; comparing at indexes 60/90/150 covers
    several session boundaries while still respecting the rule per row."""
    _check_prefix("vwap", lambda lf: vwap(lf, anchor="session"), n=n, k=k, tol=1e-7)
