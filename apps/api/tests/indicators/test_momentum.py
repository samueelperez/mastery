"""MACD and Bollinger Bands."""

from __future__ import annotations

import math
import statistics

import pytest

from app.indicators.momentum import bbands, macd
from tests.indicators.conftest import make_lf, py_ema


def test_macd_components_consistent(synthetic_closes: list[float]) -> None:
    df = macd(make_lf(synthetic_closes)).collect()
    fast = py_ema(synthetic_closes, 12)
    slow = py_ema(synthetic_closes, 26)
    expected_macd = [
        None if (f is None or s is None) else f - s
        for f, s in zip(fast, slow, strict=True)
    ]
    actual_macd = df["macd"].to_list()
    for i, (a, e) in enumerate(zip(actual_macd, expected_macd, strict=True)):
        if e is None:
            assert a is None, f"row {i}: expected None"
        else:
            assert a is not None
            assert math.isclose(a, e, abs_tol=1e-9)


def test_macd_signal_is_ema_of_macd(synthetic_closes: list[float]) -> None:
    df = macd(make_lf(synthetic_closes)).collect()
    macd_vals = df["macd"].to_list()
    # Drop leading nulls so we can recompute the signal from where MACD becomes valid.
    first_valid = next(i for i, v in enumerate(macd_vals) if v is not None)
    macd_clean = [float(v) for v in macd_vals[first_valid:]]
    expected_signal_tail = py_ema(macd_clean, 9)
    actual_signal = df["macd_signal"].to_list()
    for i, sig in enumerate(actual_signal[first_valid:]):
        e = expected_signal_tail[i]
        if e is None:
            assert sig is None
        else:
            assert sig is not None
            assert math.isclose(sig, e, abs_tol=1e-9), f"signal[{first_valid + i}]"


def test_macd_hist_equals_macd_minus_signal(synthetic_closes: list[float]) -> None:
    df = macd(make_lf(synthetic_closes)).collect()
    for i in range(df.height):
        m, s, h = df["macd"][i], df["macd_signal"][i], df["macd_hist"][i]
        if m is None or s is None:
            assert h is None
        else:
            assert h is not None
            assert math.isclose(h, m - s, abs_tol=1e-12)


def test_bbands_mid_is_sma(synthetic_closes: list[float]) -> None:
    df = bbands(make_lf(synthetic_closes), length=20).collect()
    actual = df["bb_mid"].to_list()
    for i in range(df.height):
        if i + 1 < 20:
            assert actual[i] is None
        else:
            window = synthetic_closes[i - 19 : i + 1]
            expected = sum(window) / 20
            assert actual[i] is not None
            assert math.isclose(float(actual[i]), expected, abs_tol=1e-9)


def test_bbands_width_2sigma(synthetic_closes: list[float]) -> None:
    df = bbands(make_lf(synthetic_closes), length=20, stds=2.0).collect()
    for i in range(df.height):
        upper = df["bb_upper"][i]
        lower = df["bb_lower"][i]
        if upper is None:
            assert lower is None
            continue
        window = synthetic_closes[i - 19 : i + 1]
        # Population std (ddof=0) to match Polars rolling_std default for indicators.
        std_pop = statistics.pstdev(window)
        mid = sum(window) / 20
        assert math.isclose(float(upper), mid + 2 * std_pop, abs_tol=1e-7)
        assert math.isclose(float(lower), mid - 2 * std_pop, abs_tol=1e-7)


@pytest.mark.parametrize("length", [10, 20])
def test_bbands_bw_is_band_over_mid(synthetic_closes: list[float], length: int) -> None:
    df = bbands(make_lf(synthetic_closes), length=length).collect()
    for i in range(df.height):
        bw, mid, up, lo = df["bb_bw"][i], df["bb_mid"][i], df["bb_upper"][i], df["bb_lower"][i]
        if bw is None:
            continue
        assert math.isclose(float(bw), (up - lo) / mid, abs_tol=1e-12)
