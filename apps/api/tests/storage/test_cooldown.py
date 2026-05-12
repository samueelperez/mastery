"""Pure-function tests for B.3 cooldown logic.

The DB-bound `should_pause_scout` is exercised in smoke E2E; here we cover
`evaluate_streak` and `evaluate_cooldown_verdict` with synthetic closures so
the edge cases are pinned (streak break on TP, expiry from window, multi-scope
precedence).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.storage.cooldown import (
    CONSEC_LOSSES_GLOBAL,
    CONSEC_LOSSES_SYMBOL,
    PAUSE_GLOBAL_H,
    PAUSE_SYMBOL_H,
    evaluate_cooldown_verdict,
    evaluate_streak,
)


NOW = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)


def _loss(hours_ago: float, symbol: str = "BTCUSDT") -> tuple[datetime, float, str]:
    return (NOW - timedelta(hours=hours_ago), -1.0, symbol)


def _win(hours_ago: float, symbol: str = "BTCUSDT") -> tuple[datetime, float, str]:
    return (NOW - timedelta(hours=hours_ago), 1.5, symbol)


def _be(hours_ago: float, symbol: str = "BTCUSDT") -> tuple[datetime, float, str]:
    return (NOW - timedelta(hours=hours_ago), 0.05, symbol)


# -----------------------------------------------------------------------------
# evaluate_streak
# -----------------------------------------------------------------------------


def test_streak_counts_consecutive_losses_in_window() -> None:
    """3 SL in last 4h → streak=3."""
    closures = [_loss(0.5), _loss(1.5), _loss(3.0)]
    streak = evaluate_streak(
        closures=closures, now=NOW, window_hours=4, min_consecutive_losses=2,
    )
    assert streak == 3


def test_streak_breaks_on_win() -> None:
    """SL → SL → WIN → SL (most-recent-first). The most recent run of
    losses is just the first SL (the WIN breaks). Streak=1."""
    closures = [_loss(0.5), _win(1.0), _loss(2.0), _loss(3.0)]
    streak = evaluate_streak(
        closures=closures, now=NOW, window_hours=8, min_consecutive_losses=2,
    )
    assert streak == 1


def test_streak_breaks_on_breakeven_positive() -> None:
    """r_mult=0.05 (small positive) counts as a non-loss → breaks streak."""
    closures = [_loss(0.5), _be(1.0), _loss(2.0)]
    streak = evaluate_streak(
        closures=closures, now=NOW, window_hours=8, min_consecutive_losses=2,
    )
    assert streak == 1


def test_streak_ignores_closures_outside_window() -> None:
    """3 SL spaced 5h apart; window=4h → only the most recent counts."""
    closures = [_loss(0.5), _loss(5.5), _loss(10.5)]
    streak = evaluate_streak(
        closures=closures, now=NOW, window_hours=4, min_consecutive_losses=2,
    )
    assert streak == 1


def test_streak_zero_when_no_recent_closures() -> None:
    streak = evaluate_streak(
        closures=[], now=NOW, window_hours=4, min_consecutive_losses=2,
    )
    assert streak == 0


def test_streak_loss_at_exactly_window_boundary_included() -> None:
    """closed_at == now - window_hours is INSIDE the window (inclusive lower bound).
    A loss that closed exactly `window_hours` ago counts toward the streak —
    this is the more conservative reading: better to trigger one tick too early
    than one too late, since the symptom is a vulnerable user."""
    closures = [(NOW - timedelta(hours=4), -1.0, "BTCUSDT")]
    streak = evaluate_streak(
        closures=closures, now=NOW, window_hours=4, min_consecutive_losses=2,
    )
    assert streak == 1


# -----------------------------------------------------------------------------
# evaluate_cooldown_verdict — symbol scope
# -----------------------------------------------------------------------------


def test_symbol_pause_fires_when_2_consec_sl_in_4h() -> None:
    sym_closures = [_loss(0.5, "BTCUSDT"), _loss(2.0, "BTCUSDT")]
    glb_closures = sym_closures.copy()
    verdict = evaluate_cooldown_verdict(
        symbol_closures=sym_closures,
        global_closures=glb_closures,
        now=NOW,
    )
    assert verdict.paused is True
    assert verdict.scope == "symbol"
    assert verdict.consecutive_losses == 2
    assert verdict.ends_at == NOW + timedelta(hours=PAUSE_SYMBOL_H)


def test_no_pause_when_single_sl_in_symbol() -> None:
    sym_closures = [_loss(1.0, "BTCUSDT")]
    verdict = evaluate_cooldown_verdict(
        symbol_closures=sym_closures,
        global_closures=sym_closures,
        now=NOW,
    )
    assert verdict.paused is False
    assert verdict.scope == "none"


def test_no_pause_when_recent_win_breaks_streak() -> None:
    """SL → WIN → SL → SL (most-recent-first): streak=1, no pause."""
    sym_closures = [_loss(0.5), _win(1.0), _loss(2.0), _loss(3.0)]
    verdict = evaluate_cooldown_verdict(
        symbol_closures=sym_closures,
        global_closures=sym_closures,
        now=NOW,
    )
    assert verdict.paused is False


# -----------------------------------------------------------------------------
# evaluate_cooldown_verdict — global scope
# -----------------------------------------------------------------------------


def test_global_pause_fires_with_3_consec_sl_across_symbols() -> None:
    """3 SL in 8h on mixed symbols → global pause (symbol-specific check
    might not fire because they're on different symbols)."""
    glb_closures = [
        _loss(0.5, "BTCUSDT"),
        _loss(2.0, "ETHUSDT"),
        _loss(5.0, "SOLUSDT"),
    ]
    # Symbol scope sees only BTCUSDT closures (1 loss).
    sym_closures = [_loss(0.5, "BTCUSDT")]
    verdict = evaluate_cooldown_verdict(
        symbol_closures=sym_closures,
        global_closures=glb_closures,
        now=NOW,
    )
    assert verdict.paused is True
    assert verdict.scope == "global"
    assert verdict.consecutive_losses == 3
    assert verdict.ends_at == NOW + timedelta(hours=PAUSE_GLOBAL_H)


def test_global_wins_when_both_scopes_fire() -> None:
    """Both 2 SL in 4h on BTCUSDT AND 3 SL globally in 8h → global priority
    (longer pause is more restrictive)."""
    sym_closures = [_loss(0.5, "BTCUSDT"), _loss(2.0, "BTCUSDT")]
    glb_closures = [
        _loss(0.5, "BTCUSDT"),
        _loss(2.0, "BTCUSDT"),
        _loss(5.0, "ETHUSDT"),
    ]
    verdict = evaluate_cooldown_verdict(
        symbol_closures=sym_closures,
        global_closures=glb_closures,
        now=NOW,
    )
    assert verdict.paused is True
    assert verdict.scope == "global"
    assert verdict.ends_at == NOW + timedelta(hours=PAUSE_GLOBAL_H)


# -----------------------------------------------------------------------------
# Sanity: constants are sane
# -----------------------------------------------------------------------------


def test_thresholds_are_above_one() -> None:
    """Defensive: a streak of just 1 loss should NEVER trigger a cooldown —
    that would block the scout on the first SL."""
    assert CONSEC_LOSSES_SYMBOL >= 2
    assert CONSEC_LOSSES_GLOBAL >= 2
