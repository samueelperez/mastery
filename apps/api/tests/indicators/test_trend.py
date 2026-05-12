"""ADX (Wilder)."""

from __future__ import annotations

import math

from app.market.indicators.trend import adx
from tests.indicators.conftest import make_lf, py_ewm


def _py_adx(
    h: list[float], l_: list[float], c: list[float], length: int = 14
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Reference ADX, +DI, -DI computed in pure Python."""
    n = len(c)
    plus_dm: list[float] = [0.0]
    minus_dm: list[float] = [0.0]
    tr: list[float] = [h[0] - l_[0]]
    for i in range(1, n):
        up = h[i] - h[i - 1]
        down = l_[i - 1] - l_[i]
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
        tr.append(max(h[i] - l_[i], abs(h[i] - c[i - 1]), abs(l_[i] - c[i - 1])))
    alpha = 1 / length
    atr_w = py_ewm(tr, alpha=alpha, min_samples=length)
    plus_dm_w = py_ewm(plus_dm, alpha=alpha, min_samples=length)
    minus_dm_w = py_ewm(minus_dm, alpha=alpha, min_samples=length)
    plus_di: list[float | None] = []
    minus_di: list[float | None] = []
    dx: list[float] = []
    for a, p, m in zip(atr_w, plus_dm_w, minus_dm_w, strict=True):
        if a is None or p is None or m is None or a == 0:
            plus_di.append(None)
            minus_di.append(None)
            dx.append(0.0)
            continue
        pdi = 100.0 * p / a
        mdi = 100.0 * m / a
        plus_di.append(pdi)
        minus_di.append(mdi)
        denom = pdi + mdi
        dx.append(100.0 * abs(pdi - mdi) / denom if denom else 0.0)
    adx_v = py_ewm(dx, alpha=alpha, min_samples=length)
    return plus_di, minus_di, adx_v


def test_adx_matches_pure_python(synthetic_ohlc: dict[str, list[float]]) -> None:
    lf = make_lf(
        synthetic_ohlc["c"],
        highs=synthetic_ohlc["h"],
        lows=synthetic_ohlc["l"],
    )
    df = adx(lf, length=14).collect()
    e_pdi, e_mdi, e_adx = _py_adx(synthetic_ohlc["h"], synthetic_ohlc["l"], synthetic_ohlc["c"], 14)
    for i in range(df.height):
        a_pdi = df["plus_di"][i]
        a_mdi = df["minus_di"][i]
        a_adx = df["adx"][i]
        if e_pdi[i] is None:
            assert a_pdi is None
        else:
            assert a_pdi is not None
            assert math.isclose(float(a_pdi), e_pdi[i], abs_tol=1e-7)
        if e_mdi[i] is None:
            assert a_mdi is None
        else:
            assert a_mdi is not None
            assert math.isclose(float(a_mdi), e_mdi[i], abs_tol=1e-7)
        if e_adx[i] is None:
            assert a_adx is None
        else:
            assert a_adx is not None
            assert math.isclose(float(a_adx), e_adx[i], abs_tol=1e-7)


def test_adx_strong_trend_above_25() -> None:
    """A monotonically rising series should produce an ADX above 25 once warm."""
    closes = [100.0 + i * 1.0 for i in range(60)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    df = adx(make_lf(closes, highs=highs, lows=lows), length=14).collect()
    last = df["adx"][-1]
    assert last is not None
    assert last > 25.0, f"expected strong-trend ADX > 25, got {last}"


def test_adx_di_signs_track_direction() -> None:
    """In an uptrend +DI should dominate -DI."""
    closes = [100.0 + i * 1.0 for i in range(60)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    df = adx(make_lf(closes, highs=highs, lows=lows), length=14).collect()
    pdi = df["plus_di"][-1]
    mdi = df["minus_di"][-1]
    assert pdi is not None and mdi is not None
    assert pdi > mdi
