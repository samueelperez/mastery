"""Pure-function tests for `risk_manager.compute_risk_actions` and helpers.

The DB-application path (`apply_risk_action_to_db`) and the ATR fetcher are
exercised in the end-to-end smoke run; here we cover the deterministic
decision logic that's the heart of B.1.

Each rule (BE move, trailing, time stop) is tested in isolation plus a few
interaction tests (BE + trailing in the same candle, idempotency on re-run).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.runtime.risk_manager import (
    BreakevenAction,
    TimeStopAction,
    TrailingAction,
    _is_tp1_hit,
    compute_risk_actions,
    compute_unrealized_r,
    max_hold_for_tf,
)
from app.storage.setup_repo import OpenSetupRow

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _setup(
    *,
    side: str = "long",
    status: str = "active",
    entry_px: float = 100.0,
    stop_loss_px: float | None = 95.0,
    targets: list[dict[str, Any]] | None = None,
    entry_hit_at: datetime | None = None,
    risk_state: dict[str, Any] | None = None,
    timeframe: str = "1h",
) -> OpenSetupRow:
    return OpenSetupRow(
        id="00000000-0000-0000-0000-000000000001",
        user_id="u",
        symbol="BTCUSDT",
        timeframe=timeframe,
        side=side,
        status=status,
        entry_px=entry_px,
        stop_loss_px=stop_loss_px,
        targets=targets or [],
        invalidation_conditions=[],
        expires_at=None,
        proposed_at=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        entry_hit_at=entry_hit_at,
        risk_state=risk_state or {},
    )


CANDLE_TS = datetime(2026, 5, 12, 7, 0, tzinfo=UTC)


# -----------------------------------------------------------------------------
# Pure helper coverage
# -----------------------------------------------------------------------------


def test_unrealized_r_long_in_profit() -> None:
    r = compute_unrealized_r("long", entry=100.0, sl=95.0, current_price=102.5)
    # risk = 5, gain = 2.5 → 0.5R.
    assert r == 0.5


def test_unrealized_r_long_in_drawdown() -> None:
    r = compute_unrealized_r("long", entry=100.0, sl=95.0, current_price=98.0)
    # risk = 5, gain = -2 → -0.4R.
    assert r == -0.4


def test_unrealized_r_short_in_profit() -> None:
    r = compute_unrealized_r("short", entry=100.0, sl=105.0, current_price=97.5)
    # risk = 5, gain (for shorts: entry-current) = 2.5 → 0.5R.
    assert r == 0.5


def test_unrealized_r_zero_risk_returns_zero() -> None:
    # entry == sl pathological case must not divide by zero.
    assert compute_unrealized_r("long", 100.0, 100.0, 102.0) == 0.0


def test_max_hold_for_tf_covered() -> None:
    assert max_hold_for_tf("1h", h_15m=12, h_1h=24, h_4h=72, h_1d=240) == 24
    assert max_hold_for_tf("4h", h_15m=12, h_1h=24, h_4h=72, h_1d=240) == 72


def test_max_hold_for_tf_uncovered_returns_none() -> None:
    # 1m and 5m setups shouldn't trigger time stop with a stale default.
    assert max_hold_for_tf("1m", h_15m=12, h_1h=24, h_4h=72, h_1d=240) is None


def test_is_tp1_hit_true() -> None:
    assert _is_tp1_hit(
        _setup(targets=[{"label": "TP1", "price": 110.0, "hit_at": "2026-05-12T06:00Z"}])
    )


def test_is_tp1_hit_false_when_no_targets() -> None:
    assert not _is_tp1_hit(_setup(targets=[]))


def test_is_tp1_hit_false_when_not_hit() -> None:
    assert not _is_tp1_hit(_setup(targets=[{"label": "TP1", "price": 110.0}]))


# -----------------------------------------------------------------------------
# No-op / preconditions
# -----------------------------------------------------------------------------


def test_no_actions_when_setup_pending() -> None:
    s = _setup(status="pending")
    assert (
        compute_risk_actions(
            s,
            close=110.0,
            candle_ts=CANDLE_TS,
            atr_value=None,
            move_to_be_after_r=0.5,
            trailing_atr_multiple=2.0,
            max_hold_hours=24,
        )
        == []
    )


def test_no_actions_when_stop_loss_missing() -> None:
    s = _setup(stop_loss_px=None)
    assert (
        compute_risk_actions(
            s,
            close=110.0,
            candle_ts=CANDLE_TS,
            atr_value=None,
            move_to_be_after_r=0.5,
            trailing_atr_multiple=2.0,
            max_hold_hours=24,
        )
        == []
    )


def test_no_actions_when_time_stopped_flag_set() -> None:
    """Defensive: once time-stopped, never re-emit actions even if status
    somehow remained 'active' (it shouldn't — but the flag short-circuits)."""
    s = _setup(
        risk_state={"time_stopped": True},
        entry_hit_at=CANDLE_TS - timedelta(hours=48),
    )
    assert (
        compute_risk_actions(
            s,
            close=110.0,
            candle_ts=CANDLE_TS,
            atr_value=None,
            move_to_be_after_r=0.5,
            trailing_atr_multiple=2.0,
            max_hold_hours=24,
        )
        == []
    )


# -----------------------------------------------------------------------------
# Breakeven move
# -----------------------------------------------------------------------------


def test_be_move_fires_when_unrealized_reaches_threshold() -> None:
    """Long setup entry=100, SL=95, close=102.5 (= 0.5R unrealized).
    BE threshold = 0.5R → fires."""
    s = _setup(side="long", entry_px=100.0, stop_loss_px=95.0)
    actions = compute_risk_actions(
        s,
        close=102.5,
        candle_ts=CANDLE_TS,
        atr_value=None,
        move_to_be_after_r=0.5,
        trailing_atr_multiple=2.0,
        max_hold_hours=None,
    )
    assert len(actions) == 1
    assert isinstance(actions[0], BreakevenAction)
    assert actions[0].new_sl == 100.0
    assert actions[0].unrealized_r == 0.5


def test_be_move_does_not_fire_below_threshold() -> None:
    s = _setup(side="long", entry_px=100.0, stop_loss_px=95.0)
    actions = compute_risk_actions(
        s,
        close=101.5,  # 0.3R, below 0.5 threshold
        candle_ts=CANDLE_TS,
        atr_value=None,
        move_to_be_after_r=0.5,
        trailing_atr_multiple=2.0,
        max_hold_hours=None,
    )
    assert actions == []


def test_be_move_idempotent_when_already_moved() -> None:
    """risk_state.breakeven_moved=True → BE rule must NOT re-emit
    BreakevenAction on subsequent candles, even if unrealized R is huge."""
    s = _setup(
        side="long",
        entry_px=100.0,
        stop_loss_px=100.0,  # already at entry post-BE
        risk_state={"breakeven_moved": True},
    )
    actions = compute_risk_actions(
        s,
        close=120.0,  # massive profit
        candle_ts=CANDLE_TS,
        atr_value=None,
        move_to_be_after_r=0.5,
        trailing_atr_multiple=2.0,
        max_hold_hours=None,
    )
    # No BE action; could be a trailing action only if TP1 was hit, which it
    # isn't here, so the list should be empty.
    assert actions == []


def test_be_move_short_side() -> None:
    """Short setup: entry=100, SL=105, close=97.5 → 0.5R unrealized."""
    s = _setup(side="short", entry_px=100.0, stop_loss_px=105.0)
    actions = compute_risk_actions(
        s,
        close=97.5,
        candle_ts=CANDLE_TS,
        atr_value=None,
        move_to_be_after_r=0.5,
        trailing_atr_multiple=2.0,
        max_hold_hours=None,
    )
    assert len(actions) == 1
    assert isinstance(actions[0], BreakevenAction)
    assert actions[0].new_sl == 100.0


# -----------------------------------------------------------------------------
# Trailing stop
# -----------------------------------------------------------------------------


def test_trailing_does_not_fire_before_tp1_hit() -> None:
    """ATR available but TP1 not yet hit → no trailing action."""
    s = _setup(
        side="long",
        entry_px=100.0,
        stop_loss_px=95.0,
        targets=[{"label": "TP1", "price": 110.0}],  # no hit_at
    )
    actions = compute_risk_actions(
        s,
        close=108.0,
        candle_ts=CANDLE_TS,
        atr_value=2.0,
        move_to_be_after_r=0.5,
        trailing_atr_multiple=2.0,
        max_hold_hours=None,
    )
    # BE may still fire (unrealized 1.6R > 0.5); ensure no TrailingAction in list.
    assert all(not isinstance(a, TrailingAction) for a in actions)


def test_trailing_fires_after_tp1_when_atr_offset_is_above_current_sl() -> None:
    """TP1 hit. Long, entry=100, current SL=100 (post-BE), close=120, ATR=2,
    multiple=2 → candidate_sl = 120 - 4 = 116. 116 > 100 → trailing fires."""
    s = _setup(
        side="long",
        entry_px=100.0,
        stop_loss_px=100.0,
        targets=[{"label": "TP1", "price": 110.0, "hit_at": "2026-05-12T06:00Z"}],
        risk_state={"breakeven_moved": True},
    )
    actions = compute_risk_actions(
        s,
        close=120.0,
        candle_ts=CANDLE_TS,
        atr_value=2.0,
        move_to_be_after_r=0.5,
        trailing_atr_multiple=2.0,
        max_hold_hours=None,
    )
    trailing = [a for a in actions if isinstance(a, TrailingAction)]
    assert len(trailing) == 1
    assert trailing[0].new_sl == 116.0


def test_trailing_does_not_widen_existing_stop() -> None:
    """If the candidate SL would LOWER the current stop, no action (ratchet
    must only tighten)."""
    s = _setup(
        side="long",
        entry_px=100.0,
        stop_loss_px=115.0,  # already trailed up
        targets=[{"label": "TP1", "price": 110.0, "hit_at": "2026-05-12T06:00Z"}],
        risk_state={"breakeven_moved": True, "trailing_active": True},
    )
    actions = compute_risk_actions(
        s,
        close=118.0,  # candidate = 118 - 4 = 114 < 115
        candle_ts=CANDLE_TS,
        atr_value=2.0,
        move_to_be_after_r=0.5,
        trailing_atr_multiple=2.0,
        max_hold_hours=None,
    )
    assert all(not isinstance(a, TrailingAction) for a in actions)


def test_trailing_short_side() -> None:
    """Short: entry=100, current SL=100 (post-BE), close=80, ATR=2, mult=2 →
    candidate_sl = 80 + 4 = 84. 84 < 100 → trailing fires (tightens short)."""
    s = _setup(
        side="short",
        entry_px=100.0,
        stop_loss_px=100.0,
        targets=[{"label": "TP1", "price": 90.0, "hit_at": "2026-05-12T06:00Z"}],
        risk_state={"breakeven_moved": True},
    )
    actions = compute_risk_actions(
        s,
        close=80.0,
        candle_ts=CANDLE_TS,
        atr_value=2.0,
        move_to_be_after_r=0.5,
        trailing_atr_multiple=2.0,
        max_hold_hours=None,
    )
    trailing = [a for a in actions if isinstance(a, TrailingAction)]
    assert len(trailing) == 1
    assert trailing[0].new_sl == 84.0


def test_trailing_skipped_when_atr_unavailable() -> None:
    """No ATR (fetch failed / not enough warm-up) → no trailing."""
    s = _setup(
        side="long",
        entry_px=100.0,
        stop_loss_px=100.0,
        targets=[{"label": "TP1", "price": 110.0, "hit_at": "2026-05-12T06:00Z"}],
        risk_state={"breakeven_moved": True},
    )
    actions = compute_risk_actions(
        s,
        close=120.0,
        candle_ts=CANDLE_TS,
        atr_value=None,
        move_to_be_after_r=0.5,
        trailing_atr_multiple=2.0,
        max_hold_hours=None,
    )
    assert all(not isinstance(a, TrailingAction) for a in actions)


# -----------------------------------------------------------------------------
# Time stop
# -----------------------------------------------------------------------------


def test_time_stop_fires_when_held_exceeds_max() -> None:
    s = _setup(
        timeframe="1h",
        entry_hit_at=CANDLE_TS - timedelta(hours=25),  # 1h past max=24
    )
    actions = compute_risk_actions(
        s,
        close=98.0,
        candle_ts=CANDLE_TS,
        atr_value=None,
        move_to_be_after_r=0.5,
        trailing_atr_multiple=2.0,
        max_hold_hours=24,
    )
    assert len(actions) == 1
    assert isinstance(actions[0], TimeStopAction)
    assert actions[0].exit_px == 98.0
    assert actions[0].held_hours == 25.0


def test_time_stop_short_circuits_other_rules() -> None:
    """Even if BE/trailing would otherwise fire, TimeStop is terminal and
    returned alone."""
    s = _setup(
        side="long",
        entry_px=100.0,
        stop_loss_px=95.0,
        timeframe="1h",
        entry_hit_at=CANDLE_TS - timedelta(hours=25),
        targets=[{"label": "TP1", "price": 110.0, "hit_at": "2026-05-12T06:00Z"}],
    )
    actions = compute_risk_actions(
        s,
        close=115.0,  # would normally trigger BE + trailing
        candle_ts=CANDLE_TS,
        atr_value=2.0,
        move_to_be_after_r=0.5,
        trailing_atr_multiple=2.0,
        max_hold_hours=24,
    )
    assert len(actions) == 1
    assert isinstance(actions[0], TimeStopAction)


def test_time_stop_skipped_when_not_yet_due() -> None:
    s = _setup(
        timeframe="1h",
        entry_hit_at=CANDLE_TS - timedelta(hours=23),
    )
    actions = compute_risk_actions(
        s,
        close=102.5,
        candle_ts=CANDLE_TS,
        atr_value=None,
        move_to_be_after_r=0.5,
        trailing_atr_multiple=2.0,
        max_hold_hours=24,
    )
    assert all(not isinstance(a, TimeStopAction) for a in actions)


def test_time_stop_skipped_when_max_hold_none() -> None:
    """TF not in the covered map (e.g. 1m) → no time stop ever."""
    s = _setup(
        timeframe="1m",
        entry_hit_at=CANDLE_TS - timedelta(days=10),  # ages
    )
    actions = compute_risk_actions(
        s,
        close=102.5,
        candle_ts=CANDLE_TS,
        atr_value=None,
        move_to_be_after_r=0.5,
        trailing_atr_multiple=2.0,
        max_hold_hours=None,
    )
    assert all(not isinstance(a, TimeStopAction) for a in actions)


# -----------------------------------------------------------------------------
# Interaction: BE + trailing on the same candle
# -----------------------------------------------------------------------------


def test_be_and_trailing_can_fire_in_same_candle() -> None:
    """Edge case: TP1 just hit and price ran far enough that BE threshold is
    crossed AND trailing offset improves on the BE SL. Both should be emitted
    in order BE → Trailing so the trailing decision uses the post-BE SL as
    the baseline."""
    s = _setup(
        side="long",
        entry_px=100.0,
        stop_loss_px=95.0,  # not yet moved to BE
        targets=[{"label": "TP1", "price": 110.0, "hit_at": "2026-05-12T06:00Z"}],
    )
    actions = compute_risk_actions(
        s,
        close=115.0,
        candle_ts=CANDLE_TS,
        atr_value=2.0,
        move_to_be_after_r=0.5,
        trailing_atr_multiple=2.0,
        max_hold_hours=None,
    )
    # Expect: BreakevenAction(new_sl=100) then TrailingAction(new_sl=111)
    # (115 - 2*2 = 111, which is > 100 baseline post-BE → trailing fires).
    assert len(actions) == 2
    assert isinstance(actions[0], BreakevenAction)
    assert actions[0].new_sl == 100.0
    assert isinstance(actions[1], TrailingAction)
    assert actions[1].new_sl == 111.0
