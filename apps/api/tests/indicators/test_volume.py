"""VWAP — session-anchored at UTC day boundary."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from app.indicators.volume import vwap
from tests.indicators.conftest import make_lf


def _py_vwap_session(
    ts: list[datetime], h: list[float], l_: list[float], c: list[float], v: list[float]
) -> list[float]:
    """Cumulative typical*v / cumulative v, reset on UTC day boundary."""
    out: list[float] = []
    bucket_pv: dict[str, float] = {}
    bucket_v: dict[str, float] = {}
    for i in range(len(ts)):
        key = ts[i].strftime("%Y-%m-%d")
        typ = (h[i] + l_[i] + c[i]) / 3.0
        bucket_pv[key] = bucket_pv.get(key, 0.0) + typ * v[i]
        bucket_v[key] = bucket_v.get(key, 0.0) + v[i]
        out.append(bucket_pv[key] / bucket_v[key])
    return out


def test_vwap_resets_at_utc_day_boundary() -> None:
    # Two-day series at 1h interval — 48 candles total.
    closes = [100.0 + i * 0.1 for i in range(48)]
    highs = [c + 0.2 for c in closes]
    lows = [c - 0.2 for c in closes]
    volumes = [10.0] * 48
    start = datetime(2025, 1, 1, tzinfo=UTC)

    lf = make_lf(closes, highs=highs, lows=lows, volumes=volumes, start=start)
    df = vwap(lf, anchor="session").collect()
    actual = df["vwap"].to_list()

    ts = [start + timedelta(hours=i) for i in range(48)]
    expected = _py_vwap_session(ts, highs, lows, closes, volumes)

    for i in range(48):
        assert actual[i] is not None
        assert math.isclose(float(actual[i]), expected[i], abs_tol=1e-9), (
            f"row {i}: expected {expected[i]}, got {actual[i]}"
        )


def test_vwap_first_row_equals_typical_price() -> None:
    closes = [100.0]
    df = vwap(make_lf(closes), anchor="session").collect()
    typical = (100.5 + 99.5 + 100.0) / 3.0  # default highs/lows/closes
    assert math.isclose(float(df["vwap"][0]), typical, abs_tol=1e-12)


def test_vwap_anchor_none_is_cumulative() -> None:
    """anchor='none' should never reset, so vwap is monotonically biased toward
    the running typical mean even across day boundaries."""
    closes = [10.0] * 48  # constant typical price
    start = datetime(2025, 1, 1, tzinfo=UTC)
    lf = make_lf(closes, start=start)
    df = vwap(lf, anchor="none").collect()
    last = df["vwap"][-1]
    typical = (10.5 + 9.5 + 10.0) / 3.0
    assert math.isclose(float(last), typical, abs_tol=1e-9)
