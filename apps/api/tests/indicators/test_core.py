"""Verify SMA, EMA, RSI, ATR vs pure-Python reference impls."""

from __future__ import annotations

import math

import pytest

from app.market.indicators.core import atr, ema, rsi, sma
from tests.indicators.conftest import make_lf, py_atr, py_ema, py_rsi, py_sma


def _floats_equal(actual: list[float | None], expected: list[float | None], tol: float) -> None:
    assert len(actual) == len(expected), f"length mismatch: {len(actual)} vs {len(expected)}"
    for i, (a, e) in enumerate(zip(actual, expected, strict=True)):
        if e is None:
            assert a is None, f"row {i}: expected None, got {a}"
        else:
            assert a is not None, f"row {i}: expected {e}, got None"
            assert math.isclose(a, e, rel_tol=tol, abs_tol=tol), (
                f"row {i}: expected {e}, got {a}, diff={abs(a - e)}"
            )


def test_sma_matches_pure_python(synthetic_closes: list[float]) -> None:
    df = sma(make_lf(synthetic_closes), length=5).collect()
    expected = py_sma(synthetic_closes, 5)
    _floats_equal(df["sma_5"].to_list(), expected, tol=1e-9)


def test_sma_first_window_minus_one_rows_are_null(synthetic_closes: list[float]) -> None:
    df = sma(make_lf(synthetic_closes), length=5).collect()
    head = df["sma_5"].to_list()[:4]
    assert all(v is None for v in head)
    assert df["sma_5"][4] is not None


def test_ema_matches_pure_python(synthetic_closes: list[float]) -> None:
    df = ema(make_lf(synthetic_closes), length=10).collect()
    expected = py_ema(synthetic_closes, 10)
    _floats_equal(df["ema_10"].to_list(), expected, tol=1e-9)


def test_ema_uses_adjust_false_recursion() -> None:
    """Sanity check that we're NOT using `adjust=True` (the pandas/scipy default).

    With adjust=False, EMA at index `length-1` is α·X[length-1] + (1-α)·EMA[length-2].
    With adjust=True, EMA is a weighted average of all preceding values — produces
    a noticeably different result.
    """
    closes = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    out = ema(make_lf(closes), length=5).collect()["ema_5"].to_list()
    expected = py_ema(closes, 5)
    _floats_equal(out, expected, tol=1e-9)


@pytest.mark.parametrize("length", [7, 14, 21])
def test_rsi_matches_pure_python(synthetic_closes: list[float], length: int) -> None:
    df = rsi(make_lf(synthetic_closes), length=length).collect()
    expected = py_rsi(synthetic_closes, length)
    _floats_equal(df[f"rsi_{length}"].to_list(), expected, tol=1e-7)


def test_rsi_bounded_0_100(synthetic_closes: list[float]) -> None:
    df = rsi(make_lf(synthetic_closes), length=14).collect()
    vals = [v for v in df["rsi_14"].to_list() if v is not None]
    assert all(0.0 <= v <= 100.0 for v in vals)


def test_rsi_uptrend_above_50(synthetic_closes: list[float]) -> None:
    """The synthetic series is mostly trending up — final RSI should be > 50."""
    df = rsi(make_lf(synthetic_closes), length=14).collect()
    last = df["rsi_14"][-1]
    assert last is not None and last > 50.0


def test_rsi_pure_pump_returns_100() -> None:
    """All-up sequence: avg_loss=0, classic 100/(1+RS) divides by 0 and gives
    inf/null. The robust formulation `100·avg_gain / (avg_gain+avg_loss)` must
    return 100 cleanly. Regresión del bug B1 reportado en la auditoría."""
    closes = [float(i) for i in range(50)]  # monotónico estrictamente alcista
    df = rsi(make_lf(closes), length=14).collect()
    last = df["rsi_14"][-1]
    assert last is not None
    assert math.isclose(float(last), 100.0, abs_tol=1e-9)


def test_rsi_pure_dump_returns_0() -> None:
    """All-down sequence: avg_gain=0 → RSI=0. Sin div-by-0."""
    closes = [float(50 - i) for i in range(50)]
    df = rsi(make_lf(closes), length=14).collect()
    last = df["rsi_14"][-1]
    assert last is not None
    assert math.isclose(float(last), 0.0, abs_tol=1e-9)


def test_rsi_flat_series_returns_null_post_warmup() -> None:
    """Precio totalmente plano: avg_gain == avg_loss == 0. No hay momentum
    medible — devolvemos null (no inventamos un valor neutral 50)."""
    closes = [100.0] * 50
    df = rsi(make_lf(closes), length=14).collect()
    last = df["rsi_14"][-1]
    assert last is None


@pytest.mark.parametrize("length", [7, 14])
def test_atr_matches_pure_python(synthetic_ohlc: dict[str, list[float]], length: int) -> None:
    df = atr(
        make_lf(
            synthetic_ohlc["c"],
            highs=synthetic_ohlc["h"],
            lows=synthetic_ohlc["l"],
            opens=synthetic_ohlc["o"],
            volumes=synthetic_ohlc["v"],
        ),
        length=length,
    ).collect()
    expected = py_atr(synthetic_ohlc["h"], synthetic_ohlc["l"], synthetic_ohlc["c"], length)
    _floats_equal(df[f"atr_{length}"].to_list(), expected, tol=1e-7)


def test_atr_strictly_positive(synthetic_ohlc: dict[str, list[float]]) -> None:
    df = atr(
        make_lf(
            synthetic_ohlc["c"],
            highs=synthetic_ohlc["h"],
            lows=synthetic_ohlc["l"],
        ),
        length=14,
    ).collect()
    vals = [v for v in df["atr_14"].to_list() if v is not None]
    assert all(v > 0 for v in vals)
