"""A.6 — Pure tests for basis tool helpers.

Network calls (spot/perp tickers, OHLCV) and Redis caching are E2E;
this file pins the policy logic that decides the regime label:

- `compute_basis_pct` — (perp-spot)/spot×100 with zero-spot guard.
- `percentile` — linear-interpolated quantile without numpy.
- `classify_basis_regime` — combines level + 30d percentile band.
- `build_interpretation` — surfaces the regime in human-readable prose.
"""

from __future__ import annotations

import pytest

from app.agent.tools.basis import (
    build_interpretation,
    classify_basis_regime,
    compute_basis_pct,
    percentile,
)
from app.data.spot_adapter import _to_spot_symbol

# ----------------------------------------------------------------------------
# compute_basis_pct
# ----------------------------------------------------------------------------


def test_basis_pct_positive_premium() -> None:
    # perp 100.5 vs spot 100.0 = 0.5%
    assert compute_basis_pct(spot=100.0, perp=100.5) == pytest.approx(0.5)


def test_basis_pct_negative_discount() -> None:
    assert compute_basis_pct(spot=100.0, perp=99.7) == pytest.approx(-0.3)


def test_basis_pct_zero_spot_guard() -> None:
    # Should not divide by zero
    assert compute_basis_pct(spot=0.0, perp=100.0) == 0.0


def test_basis_pct_negative_spot_treated_as_invalid() -> None:
    assert compute_basis_pct(spot=-10.0, perp=100.0) == 0.0


# ----------------------------------------------------------------------------
# percentile
# ----------------------------------------------------------------------------


def test_percentile_empty() -> None:
    assert percentile([], 0.5) == 0.0


def test_percentile_single() -> None:
    assert percentile([0.05], 0.9) == 0.05


def test_percentile_median_of_odd() -> None:
    assert percentile([1.0, 2.0, 3.0], 0.5) == pytest.approx(2.0)


def test_percentile_median_of_even_interpolates() -> None:
    # 0.5 * 3 = 1.5 → between idx 1 (2) and idx 2 (3) → 2.5
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == pytest.approx(2.5)


def test_percentile_p90_of_ten() -> None:
    # 0.9 * 9 = 8.1 → between idx 8 (90) and idx 9 (100) → 0.9*90 + 0.1*100 = 91
    values = [10.0 * i for i in range(1, 11)]  # [10, 20, ..., 100]
    assert percentile(values, 0.90) == pytest.approx(91.0)


# ----------------------------------------------------------------------------
# classify_basis_regime
# ----------------------------------------------------------------------------


def test_regime_neutral_when_basis_near_zero() -> None:
    r = classify_basis_regime(basis_pct=0.03, p90=0.20, p10=-0.20)
    assert r == "neutral"


def test_regime_premium_when_above_premium_min_but_not_extreme() -> None:
    r = classify_basis_regime(basis_pct=0.15, p90=0.40, p10=-0.20)
    assert r == "premium"


def test_regime_extreme_premium_requires_level_AND_p90() -> None:
    # Level > 0.25 AND >= P90 → extreme
    r = classify_basis_regime(basis_pct=0.30, p90=0.25, p10=-0.10)
    assert r == "extreme_premium"


def test_regime_premium_not_extreme_when_p90_higher() -> None:
    # Level 0.30 but P90 30d is 0.50 → just premium (not historically extreme)
    r = classify_basis_regime(basis_pct=0.30, p90=0.50, p10=-0.20)
    assert r == "premium"


def test_regime_discount() -> None:
    r = classify_basis_regime(basis_pct=-0.15, p90=0.10, p10=-0.40)
    assert r == "discount"


def test_regime_extreme_discount_requires_level_AND_p10() -> None:
    r = classify_basis_regime(basis_pct=-0.30, p90=0.10, p10=-0.25)
    assert r == "extreme_discount"


def test_regime_discount_not_extreme_when_p10_lower() -> None:
    # Level -0.30 but historical P10 was -0.50 → just discount
    r = classify_basis_regime(basis_pct=-0.30, p90=0.10, p10=-0.50)
    assert r == "discount"


# ----------------------------------------------------------------------------
# build_interpretation
# ----------------------------------------------------------------------------


def test_interpretation_extreme_premium_mentions_long_caliente() -> None:
    msg = build_interpretation(regime="extreme_premium", basis_pct=0.30, p90=0.25, p10=-0.10)
    assert "extremo" in msg.lower() or "extremo" in msg
    assert "long" in msg.lower() or "longs" in msg.lower()


def test_interpretation_extreme_discount_mentions_short_squeeze() -> None:
    msg = build_interpretation(regime="extreme_discount", basis_pct=-0.30, p90=0.10, p10=-0.25)
    assert "squeeze" in msg.lower() or "short" in msg.lower()


def test_interpretation_unavailable_does_not_pretend() -> None:
    msg = build_interpretation(regime="unavailable", basis_pct=0.0, p90=0.0, p10=0.0)
    assert "no" in msg.lower() and "spot" in msg.lower()


def test_interpretation_neutral_acknowledges_no_bias() -> None:
    msg = build_interpretation(regime="neutral", basis_pct=0.02, p90=0.10, p10=-0.10)
    assert "neutro" in msg.lower() or "sin sesgo" in msg.lower()


# ----------------------------------------------------------------------------
# _to_spot_symbol mapping
# ----------------------------------------------------------------------------


def test_to_spot_symbol_btc_usdt() -> None:
    assert _to_spot_symbol("BTCUSDT") == "BTC/USDT"


def test_to_spot_symbol_eth_usdt() -> None:
    assert _to_spot_symbol("ETHUSDT") == "ETH/USDT"


def test_to_spot_symbol_already_formatted_passes_through() -> None:
    assert _to_spot_symbol("SOL/USDT") == "SOL/USDT"


def test_to_spot_symbol_lowercase_normalized() -> None:
    assert _to_spot_symbol("btcusdt") == "BTC/USDT"


def test_to_spot_symbol_unknown_quote_falls_through() -> None:
    # No known quote suffix → passes through (CCXT will raise on use)
    assert _to_spot_symbol("WEIRD") == "WEIRD"
