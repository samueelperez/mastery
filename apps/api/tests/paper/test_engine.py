"""Pure-function tests for `paper.engine.simulate_fill` and
`paper.engine.compute_funding_cost_bps`.

The slippage formula has two terms (half-spread + ATR-scaled impact); these
tests pin each independently and the interaction.
"""

from __future__ import annotations

import math

import pytest

from app.paper.engine import (
    FillSimulationInput,
    compute_funding_cost_bps,
    simulate_fill,
)

# -----------------------------------------------------------------------------
# simulate_fill — entry
# -----------------------------------------------------------------------------


def test_long_entry_pays_half_spread_when_atr_is_zero() -> None:
    """0 volatility (atr=0): slippage = half-spread only. With spread=0.02%
    and intended=100, filled = 100 * (1 + 0.01%) = 100.01."""
    out = simulate_fill(
        FillSimulationInput(
            side="long",
            kind="entry",
            intended_px=100.0,
            spread_pct=0.02,
            atr_pct=0.0,
        )
    )
    assert math.isclose(out.filled_px, 100.01, rel_tol=1e-9)
    assert math.isclose(out.slippage_bps, 1.0, rel_tol=1e-9)
    assert out.fee_bps == 4.0


def test_short_entry_pays_half_spread_opposite_direction() -> None:
    """Short entries fill below intended (worse for the short)."""
    out = simulate_fill(
        FillSimulationInput(
            side="short",
            kind="entry",
            intended_px=100.0,
            spread_pct=0.02,
            atr_pct=0.0,
        )
    )
    assert math.isclose(out.filled_px, 99.99, rel_tol=1e-9)
    assert math.isclose(out.slippage_bps, 1.0, rel_tol=1e-9)


def test_atr_scaled_impact_adds_on_top_of_spread() -> None:
    """atr_pct=2% per period, latency=60s → impact = 2% * 1 * 0.3 = 0.6%.
    Plus half-spread 0.01% → total slippage = 0.61%."""
    out = simulate_fill(
        FillSimulationInput(
            side="long",
            kind="entry",
            intended_px=100.0,
            spread_pct=0.02,
            atr_pct=2.0,
            latency_seconds=60.0,
            impact_k=0.3,
        )
    )
    # 0.01% half-spread + 0.6% impact = 0.61%
    expected_px = 100.0 * (1 + 0.61 / 100)
    assert math.isclose(out.filled_px, expected_px, rel_tol=1e-9)
    assert math.isclose(out.slippage_bps, 61.0, rel_tol=1e-9)


def test_zero_spread_and_zero_atr_means_zero_slippage() -> None:
    out = simulate_fill(
        FillSimulationInput(
            side="long",
            kind="entry",
            intended_px=100.0,
            spread_pct=0.0,
            atr_pct=0.0,
        )
    )
    assert out.filled_px == 100.0
    assert out.slippage_bps == 0.0


def test_low_latency_reduces_impact() -> None:
    """latency=1s vs latency=60s with same ATR → impact at 1s should be
    1/60 of impact at 60s."""
    base = FillSimulationInput(
        side="long", kind="entry", intended_px=100.0,
        spread_pct=0.0, atr_pct=2.0, impact_k=0.3,
    )
    fast = simulate_fill(
        FillSimulationInput(**{**base.__dict__, "latency_seconds": 1.0})
    )
    slow = simulate_fill(
        FillSimulationInput(**{**base.__dict__, "latency_seconds": 60.0})
    )
    assert slow.slippage_bps > fast.slippage_bps
    # 60x ratio in impact when spread is zero.
    assert math.isclose(slow.slippage_bps / fast.slippage_bps, 60.0, rel_tol=1e-6)


# -----------------------------------------------------------------------------
# simulate_fill — exit
# -----------------------------------------------------------------------------


def test_long_exit_fills_below_intended() -> None:
    """Closing a long sells → crosses the spread DOWN → fills below intended."""
    out = simulate_fill(
        FillSimulationInput(
            side="long",
            kind="exit",
            intended_px=100.0,
            spread_pct=0.02,
            atr_pct=0.0,
        )
    )
    assert out.filled_px < 100.0
    assert math.isclose(out.filled_px, 99.99, rel_tol=1e-9)


def test_short_exit_fills_above_intended() -> None:
    """Closing a short buys back → crosses the spread UP → fills above intended."""
    out = simulate_fill(
        FillSimulationInput(
            side="short",
            kind="exit",
            intended_px=100.0,
            spread_pct=0.02,
            atr_pct=0.0,
        )
    )
    assert out.filled_px > 100.0
    assert math.isclose(out.filled_px, 100.01, rel_tol=1e-9)


def test_slippage_bps_always_non_negative() -> None:
    """Sanity: positive bps = lost edge. Should never be negative for the
    cases we model (we always cross the spread)."""
    for side in ("long", "short"):
        for kind in ("entry", "exit"):
            out = simulate_fill(
                FillSimulationInput(
                    side=side,  # type: ignore[arg-type]
                    kind=kind,  # type: ignore[arg-type]
                    intended_px=100.0,
                    spread_pct=0.02,
                    atr_pct=1.5,
                    latency_seconds=10.0,
                )
            )
            assert out.slippage_bps >= 0.0


# -----------------------------------------------------------------------------
# compute_funding_cost_bps
# -----------------------------------------------------------------------------


def test_funding_long_pays_when_funding_positive() -> None:
    # 0.01% per 8h * 3 intervals (24h hold) = 0.03% = 3 bps cost for long.
    cost = compute_funding_cost_bps(
        side="long", funding_rate_8h=0.0001, hold_hours=24.0
    )
    assert math.isclose(cost, 3.0, rel_tol=1e-6)


def test_funding_short_receives_when_funding_positive() -> None:
    # Inverse for shorts.
    cost = compute_funding_cost_bps(
        side="short", funding_rate_8h=0.0001, hold_hours=24.0
    )
    assert math.isclose(cost, -3.0, rel_tol=1e-6)


def test_funding_partial_interval_prorated() -> None:
    # 4h hold = 0.5 interval at 0.01%/8h = 0.005% = 0.5 bps.
    cost = compute_funding_cost_bps(
        side="long", funding_rate_8h=0.0001, hold_hours=4.0
    )
    assert math.isclose(cost, 0.5, rel_tol=1e-6)


def test_funding_zero_when_rate_zero() -> None:
    cost = compute_funding_cost_bps(
        side="long", funding_rate_8h=0.0, hold_hours=100.0
    )
    assert cost == 0.0
