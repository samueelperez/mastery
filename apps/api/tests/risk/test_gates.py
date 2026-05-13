"""Unit tests for the deterministic risk gates.

Each gate is a pure function; tests use ``SimpleNamespace`` fakes instead
of real ``TradeIdea`` instances to avoid pydantic validation noise on
inputs that intentionally violate the gate.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "sk-test-placeholder")

from app.core.config import get_settings
from app.risk.gates import (
    GateOutcome,
    daily_loss_gate,
    max_drawdown_gate,
    max_gross_leverage_gate,
    max_leverage_gate,
    min_expectancy_lcb_gate,
    min_factor_lcb_gate,
    min_rr_gate,
)
from app.risk.policy import evaluate_idea_input_gates

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _idea(
    *,
    direction: str = "long",
    symbol: str = "BTCUSDT",
    entry: float | None = 100.0,
    stop_loss: float | None = 99.0,
    target_price: float | None = 102.0,
    leverage_x: float | None = 2.0,
) -> SimpleNamespace:
    targets = (
        [SimpleNamespace(price=target_price)] if target_price is not None else []
    )
    return SimpleNamespace(
        direction=direction,
        symbol=symbol,
        entry=entry,
        stop_loss=stop_loss,
        targets=targets,
        leverage_x=leverage_x,
    )


# ---------------------------------------------------------------------------
# min_rr_gate
# ---------------------------------------------------------------------------


def test_min_rr_gate_passes_when_rr_clears_threshold():
    settings = get_settings()
    # R:R = 2.0 / 1.0 = 2.0; threshold = 1.5 + 0.3 (BTC slippage) = 1.8.
    out = min_rr_gate(_idea(entry=100, stop_loss=99, target_price=102), settings)
    assert isinstance(out, GateOutcome)
    assert out.passed is True
    assert out.skipped is False
    assert out.metadata["rr"] == pytest.approx(2.0)


def test_min_rr_gate_rejects_when_below_threshold():
    settings = get_settings()
    # R:R = 1.0 / 1.0 = 1.0; threshold ≈ 1.8 → fails.
    out = min_rr_gate(_idea(entry=100, stop_loss=99, target_price=101), settings)
    assert out.passed is False
    assert out.severity == "hard"
    assert out.reason is not None and "R:R" in out.reason


def test_min_rr_gate_skipped_for_no_trade_idea():
    settings = get_settings()
    out = min_rr_gate(_idea(direction="no_trade"), settings)
    assert out.skipped is True
    assert out.passed is True


def test_min_rr_gate_rejects_zero_risk():
    settings = get_settings()
    out = min_rr_gate(_idea(entry=100, stop_loss=100, target_price=102), settings)
    assert out.passed is False
    assert "risk=0" in (out.reason or "")


# ---------------------------------------------------------------------------
# max_leverage_gate
# ---------------------------------------------------------------------------


def test_max_leverage_gate_passes_at_or_below_cap():
    settings = get_settings()
    out = max_leverage_gate(_idea(leverage_x=3.0), settings)
    assert out.passed is True


def test_max_leverage_gate_rejects_above_cap():
    settings = get_settings()
    out = max_leverage_gate(_idea(leverage_x=5.0), settings)
    assert out.passed is False
    assert out.severity == "hard"


def test_max_leverage_gate_skipped_when_no_leverage_set():
    settings = get_settings()
    out = max_leverage_gate(_idea(leverage_x=None), settings)
    assert out.skipped is True


# ---------------------------------------------------------------------------
# max_gross_leverage_gate
# ---------------------------------------------------------------------------


def test_max_gross_leverage_gate_passes_within_cap():
    settings = get_settings()
    # Equity 10k, currently 1× gross, adding $1k @ 2× → 1.2× gross. Cap 1.5×.
    out = max_gross_leverage_gate(
        current_gross_leverage=1.0,
        proposed_size_usd=1_000.0,
        proposed_leverage_x=2.0,
        equity_usd=10_000.0,
        settings=settings,
    )
    assert out.passed is True
    assert out.metadata["gross_after"] == pytest.approx(1.2)


def test_max_gross_leverage_gate_rejects_breach():
    settings = get_settings()
    out = max_gross_leverage_gate(
        current_gross_leverage=1.4,
        proposed_size_usd=2_000.0,
        proposed_leverage_x=2.0,
        equity_usd=10_000.0,
        settings=settings,
    )
    assert out.passed is False
    assert out.metadata["gross_after"] > settings.max_gross_leverage


def test_max_gross_leverage_gate_rejects_non_positive_equity():
    settings = get_settings()
    out = max_gross_leverage_gate(
        current_gross_leverage=0.0,
        proposed_size_usd=1_000.0,
        proposed_leverage_x=1.0,
        equity_usd=0.0,
        settings=settings,
    )
    assert out.passed is False


# ---------------------------------------------------------------------------
# Factor / expectancy gates
# ---------------------------------------------------------------------------


def test_min_factor_lcb_passes_at_threshold():
    settings = get_settings()
    out = min_factor_lcb_gate(win_rate_lcb=0.42, settings=settings)
    assert out.passed is True
    assert out.severity == "soft"


def test_min_factor_lcb_fails_below_threshold():
    settings = get_settings()
    out = min_factor_lcb_gate(win_rate_lcb=0.30, settings=settings)
    assert out.passed is False
    assert out.severity == "soft"  # soft → confidence degradation, not reject


def test_min_expectancy_lcb_passes_above_threshold():
    settings = get_settings()
    out = min_expectancy_lcb_gate(expectancy_lcb_r=0.30, settings=settings)
    assert out.passed is True


def test_min_expectancy_lcb_fails_at_zero_expectancy():
    settings = get_settings()
    out = min_expectancy_lcb_gate(expectancy_lcb_r=0.10, settings=settings)
    assert out.passed is False
    assert out.severity == "hard"


# ---------------------------------------------------------------------------
# Portfolio-state gates
# ---------------------------------------------------------------------------


def test_daily_loss_gate_passes_under_limit():
    settings = get_settings()
    # 1% loss vs 3% limit → green.
    out = daily_loss_gate(
        realized_pnl_last_24h_usd=-100.0,
        equity_usd=10_000.0,
        settings=settings,
    )
    assert out.passed is True
    assert out.metadata["loss_pct"] == pytest.approx(1.0)


def test_daily_loss_gate_rejects_at_limit():
    settings = get_settings()
    # 3% loss exactly → fails (gate is < not ≤).
    out = daily_loss_gate(
        realized_pnl_last_24h_usd=-300.0,
        equity_usd=10_000.0,
        settings=settings,
    )
    assert out.passed is False
    assert "24h freeze required" in (out.reason or "")


def test_daily_loss_gate_treats_profit_as_pass():
    settings = get_settings()
    out = daily_loss_gate(
        realized_pnl_last_24h_usd=+500.0,
        equity_usd=10_000.0,
        settings=settings,
    )
    assert out.passed is True


def test_max_drawdown_gate_passes_under_circuit():
    settings = get_settings()
    # 5% dd from HWM, circuit 10%.
    out = max_drawdown_gate(
        current_equity_usd=9_500.0,
        high_watermark_usd=10_000.0,
        settings=settings,
    )
    assert out.passed is True


def test_max_drawdown_gate_rejects_at_circuit():
    settings = get_settings()
    out = max_drawdown_gate(
        current_equity_usd=9_000.0,
        high_watermark_usd=10_000.0,
        settings=settings,
    )
    assert out.passed is False
    assert "manual unlock required" in (out.reason or "")


def test_max_drawdown_gate_skipped_when_no_hwm_yet():
    settings = get_settings()
    out = max_drawdown_gate(
        current_equity_usd=10_000.0,
        high_watermark_usd=0.0,
        settings=settings,
    )
    assert out.skipped is True


# ---------------------------------------------------------------------------
# Policy orchestrator
# ---------------------------------------------------------------------------


def test_evaluate_idea_input_gates_green_path():
    settings = get_settings()
    report = evaluate_idea_input_gates(
        idea=_idea(entry=100, stop_loss=99, target_price=103, leverage_x=2.0),
        settings=settings,
    )
    assert report.passed is True
    assert report.hard_failures == []
    assert "all gates green" in report.reason_summary()


def test_evaluate_idea_input_gates_collects_multiple_hard_failures():
    settings = get_settings()
    # Bad R:R (1:1) AND leverage 10× → two hard failures, report.passed = False.
    report = evaluate_idea_input_gates(
        idea=_idea(entry=100, stop_loss=99, target_price=101, leverage_x=10.0),
        settings=settings,
    )
    assert report.passed is False
    names = {o.name for o in report.hard_failures}
    assert "min_rr_ratio" in names
    assert "max_leverage_per_position" in names
