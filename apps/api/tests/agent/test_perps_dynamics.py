"""A.5 — Pure tests for perps dynamics helpers.

Network + Binance adapter calls are E2E; this file pins the policy logic
that decides:
- `compute_oi_delta_pct` — % change vs N entries back.
- `compute_p90_abs` — extreme-funding threshold over the lookback window.
- `classify_oi_price_divergence` — (ΔOI, Δprice) → bucket label.
- `classify_squeeze_setup` — squeeze heuristic (long/short/none).
- `build_interpretation` — human-readable one-liner.
"""

from __future__ import annotations

import pytest

from app.agent.tools.perps_dynamics import (
    build_interpretation,
    classify_oi_price_divergence,
    classify_squeeze_setup,
    compute_oi_delta_pct,
    compute_p90_abs,
)

# ----------------------------------------------------------------------------
# compute_oi_delta_pct
# ----------------------------------------------------------------------------


def _oi_entry(amount: float) -> dict:
    return {"openInterestAmount": amount}


def test_oi_delta_basic_positive() -> None:
    history = [_oi_entry(100.0), _oi_entry(105.0), _oi_entry(110.0)]
    # current=110, past=100 (entries_back=2 means index -3 = 100). Δ=10%
    assert compute_oi_delta_pct(110.0, history, entries_back=2) == pytest.approx(10.0)


def test_oi_delta_negative() -> None:
    history = [_oi_entry(100.0), _oi_entry(95.0)]
    # current=95, past=100. Δ=-5%
    assert compute_oi_delta_pct(95.0, history, entries_back=1) == pytest.approx(-5.0)


def test_oi_delta_history_too_short_returns_zero() -> None:
    history = [_oi_entry(100.0)]
    # Need entries_back+1 = 25 entries; only have 1 → 0.0
    assert compute_oi_delta_pct(100.0, history, entries_back=24) == 0.0


def test_oi_delta_current_zero_returns_zero() -> None:
    history = [_oi_entry(100.0), _oi_entry(100.0)]
    assert compute_oi_delta_pct(0.0, history, entries_back=1) == 0.0


def test_oi_delta_past_zero_returns_zero() -> None:
    history = [_oi_entry(0.0), _oi_entry(100.0)]
    assert compute_oi_delta_pct(100.0, history, entries_back=1) == 0.0


def test_oi_delta_resolves_alternative_keys() -> None:
    # Some adapters expose `openInterest` instead of `openInterestAmount`
    history = [{"openInterest": 100.0}, {"openInterest": 105.0}]
    assert compute_oi_delta_pct(105.0, history, entries_back=1) == pytest.approx(5.0)


# ----------------------------------------------------------------------------
# compute_p90_abs
# ----------------------------------------------------------------------------


def test_p90_abs_empty() -> None:
    assert compute_p90_abs([]) == 0.0


def test_p90_abs_single_value() -> None:
    assert compute_p90_abs([0.02]) == pytest.approx(0.02)


def test_p90_abs_takes_absolute_values() -> None:
    # P90 of |[-0.05, -0.01, 0.0, 0.02, 0.05, 0.06, 0.1, 0.2, 0.3, 0.4]|
    # sorted abs = [0.0, 0.01, 0.02, 0.05, 0.05, 0.06, 0.1, 0.2, 0.3, 0.4]
    # rank = 0.9 * 9 = 8.1 → between idx 8 (0.3) and idx 9 (0.4) with frac 0.1
    # → 0.3 * 0.9 + 0.4 * 0.1 = 0.31
    out = compute_p90_abs([-0.05, -0.01, 0.0, 0.02, 0.05, 0.06, 0.1, 0.2, 0.3, 0.4])
    assert out == pytest.approx(0.31)


# ----------------------------------------------------------------------------
# classify_oi_price_divergence
# ----------------------------------------------------------------------------


def test_divergence_both_up() -> None:
    assert classify_oi_price_divergence(oi_delta_pct=5.0, price_delta_pct=2.0) == "both_up"


def test_divergence_oi_up_price_down() -> None:
    assert (
        classify_oi_price_divergence(oi_delta_pct=5.0, price_delta_pct=-2.0) == "oi_up_price_down"
    )


def test_divergence_oi_down_price_up() -> None:
    assert (
        classify_oi_price_divergence(oi_delta_pct=-3.0, price_delta_pct=1.5) == "oi_down_price_up"
    )


def test_divergence_both_down() -> None:
    assert classify_oi_price_divergence(oi_delta_pct=-3.0, price_delta_pct=-2.0) == "both_down"


def test_divergence_neutral_small_changes() -> None:
    # Both below thresholds (1.0, 0.5) → neutral
    assert classify_oi_price_divergence(oi_delta_pct=0.3, price_delta_pct=0.2) == "neutral"


# ----------------------------------------------------------------------------
# classify_squeeze_setup
# ----------------------------------------------------------------------------


def test_squeeze_none_when_funding_not_extreme() -> None:
    s = classify_squeeze_setup(
        funding_current_pct=0.02,
        funding_extreme=False,
        oi_delta_24h_pct=10.0,
        price_delta_24h_pct=0.5,
    )
    assert s == "none"


def test_squeeze_none_when_oi_not_loading() -> None:
    # Extreme funding, but OI barely moved
    s = classify_squeeze_setup(
        funding_current_pct=0.1,
        funding_extreme=True,
        oi_delta_24h_pct=2.0,  # below 5% threshold
        price_delta_24h_pct=0.5,
    )
    assert s == "none"


def test_long_squeeze_extreme_positive_oi_loading_price_flat() -> None:
    # Funding +++ extreme, OI loading, price flat (~0%)
    s = classify_squeeze_setup(
        funding_current_pct=0.12,
        funding_extreme=True,
        oi_delta_24h_pct=8.0,
        price_delta_24h_pct=0.5,  # within ±1.5
    )
    assert s == "long_squeeze"


def test_short_squeeze_extreme_negative_oi_loading_price_flat() -> None:
    s = classify_squeeze_setup(
        funding_current_pct=-0.12,
        funding_extreme=True,
        oi_delta_24h_pct=8.0,
        price_delta_24h_pct=-0.5,
    )
    assert s == "short_squeeze"


def test_long_squeeze_with_slight_price_rise_still_qualifies() -> None:
    # Price rising slightly = still longs apilados (not yet confirmed by big move)
    s = classify_squeeze_setup(
        funding_current_pct=0.12,
        funding_extreme=True,
        oi_delta_24h_pct=8.0,
        price_delta_24h_pct=3.0,  # > flat threshold but UP — still long squeeze possible
    )
    assert s == "long_squeeze"


def test_no_squeeze_when_funding_positive_but_price_collapsed() -> None:
    # Funding +++ extreme, OI loading, price already dumped -> the squeeze
    # already played out. Heuristic: only flag if price hasn't confirmed
    # the negative direction yet.
    s = classify_squeeze_setup(
        funding_current_pct=0.12,
        funding_extreme=True,
        oi_delta_24h_pct=8.0,
        price_delta_24h_pct=-3.0,
    )
    assert s == "none"


# ----------------------------------------------------------------------------
# build_interpretation
# ----------------------------------------------------------------------------


def test_interpretation_mentions_squeeze_setup() -> None:
    msg = build_interpretation(
        squeeze_setup="long_squeeze",
        oi_price_divergence="both_up",
        funding_current_pct=0.12,
        funding_p90_abs_pct=0.10,
        funding_velocity_8h_pct=0.03,
        oi_delta_24h_pct=8.0,
        price_delta_24h_pct=0.5,
    )
    assert "Long-squeeze" in msg or "long-squeeze" in msg.lower()


def test_interpretation_falls_back_to_divergence_label_when_no_squeeze() -> None:
    msg = build_interpretation(
        squeeze_setup="none",
        oi_price_divergence="both_up",
        funding_current_pct=0.01,
        funding_p90_abs_pct=0.10,
        funding_velocity_8h_pct=0.005,
        oi_delta_24h_pct=3.0,
        price_delta_24h_pct=1.5,
    )
    assert "dinero nuevo" in msg or "trend" in msg.lower()
    assert "velocity" in msg.lower()
    assert "P90" in msg


def test_interpretation_handles_zero_p90() -> None:
    """Edge case: no funding history → P90=0. Must not divide by zero."""
    msg = build_interpretation(
        squeeze_setup="none",
        oi_price_divergence="neutral",
        funding_current_pct=0.0,
        funding_p90_abs_pct=0.0,
        funding_velocity_8h_pct=0.0,
        oi_delta_24h_pct=0.0,
        price_delta_24h_pct=0.0,
    )
    assert "P90" in msg  # well-formed
