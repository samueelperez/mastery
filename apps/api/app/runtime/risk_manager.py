"""Deterministic risk rules for active setups, applied on each candle close.

Pure logic separated from `SetupRuntime` so it's testable in isolation. Three
rule types in v1, evaluated in this order:

1. **Time stop** — if `entry_hit_at` is older than the TF-specific max hold,
   close the setup at the candle's close price. Terminal — short-circuits the
   rest of the rules.
2. **Breakeven move** — when unrealized profit reaches
   `move_to_be_after_r` (default 0.5R), move `stop_loss_px` to `entry_px`.
   Idempotent via `risk_state.breakeven_moved`.
3. **Trailing stop (ATR-based)** — only after TP1 has been hit. Ratchets the
   SL toward the close by `atr * trailing_atr_multiple`. Only moves in the
   favorable direction (never widens stop).

Why this order: time stop is terminal so check it first. BE move tightens
the stop conservatively before trailing engages. Trailing only kicks in
after TP1 — early in the trade, BE protects from a reverse; once TP1 is in,
trailing locks in profit.

The runtime (`setup_runtime._evaluate_setup`) calls `compute_risk_actions`
BEFORE the standard SL/TP check so a BE move within the same candle gets
applied before the SL hit logic sees the old SL. Actions are applied via
`apply_risk_action_to_db`, which mutates `journal_trades` + writes a
`setup_events` row in one transaction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

import polars as pl
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exchanges.binance_adapter import EXCHANGE_NAME
from app.core.observability.metrics import risk_actions_total
from app.market.indicators.core import atr as compute_atr_indicator
from app.market.ohlcv.repo import fetch_range
from app.storage.setup_repo import OpenSetupRow

# -----------------------------------------------------------------------------
# Actions
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class BreakevenAction:
    new_sl: float
    unrealized_r: float


@dataclass(frozen=True)
class TrailingAction:
    new_sl: float
    atr_value: float
    candidate_offset: float


@dataclass(frozen=True)
class TimeStopAction:
    exit_px: float
    reason: str
    held_hours: float


RiskAction = BreakevenAction | TrailingAction | TimeStopAction


# -----------------------------------------------------------------------------
# Pure helpers
# -----------------------------------------------------------------------------


def max_hold_for_tf(
    timeframe: str,
    *,
    h_15m: int,
    h_1h: int,
    h_4h: int,
    h_1d: int,
) -> int | None:
    """Returns the max hold hours for a given TF, or None if the TF is not
    covered. Non-covered TFs (e.g. ``1m`` if a setup somehow lives there)
    silently skip the time-stop rule rather than triggering with a bogus default.
    """
    mapping = {"15m": h_15m, "1h": h_1h, "4h": h_4h, "1d": h_1d}
    return mapping.get(timeframe)


def compute_unrealized_r(
    side: str, entry: float, sl: float, current_price: float
) -> float:
    """Profit-and-loss expressed in R units (multiples of the initial risk).
    Positive when the trade is in profit, negative in drawdown."""
    risk = abs(entry - sl)
    if risk == 0:
        return 0.0
    if side == "long":
        return (current_price - entry) / risk
    return (entry - current_price) / risk


def _is_tp1_hit(setup: OpenSetupRow) -> bool:
    if not setup.targets:
        return False
    first = setup.targets[0]
    return isinstance(first, dict) and first.get("hit_at") is not None


def compute_risk_actions(
    setup: OpenSetupRow,
    *,
    close: float,
    candle_ts: datetime,
    atr_value: float | None,
    move_to_be_after_r: float,
    trailing_atr_multiple: float,
    max_hold_hours: int | None,
) -> list[RiskAction]:
    """Pure function: setup state + current candle → list of actions to apply.

    Returns actions in evaluation order. Caller applies them sequentially —
    each action's effect is independent except `TimeStopAction`, which is
    terminal and is the only one returned in that case.

    No-ops:
    - inactive setups → empty list.
    - missing entry/stop → empty list.
    - already-time-stopped (via risk_state.time_stopped) → empty list.
    - BE: skipped when `risk_state.breakeven_moved` is true.
    - Trailing: skipped before TP1 hit or when ATR is unavailable.
    """
    if setup.status != "active":
        return []
    if setup.stop_loss_px is None or setup.entry_px is None:
        return []

    risk_state = setup.risk_state if isinstance(setup.risk_state, dict) else {}
    if risk_state.get("time_stopped"):
        return []

    # 1. Time stop (terminal).
    if setup.entry_hit_at is not None and max_hold_hours is not None:
        # Both timestamps are tz-aware UTC; this comparison is safe.
        held = candle_ts - setup.entry_hit_at
        if held >= timedelta(hours=max_hold_hours):
            return [
                TimeStopAction(
                    exit_px=close,
                    reason=f"max_hold_{setup.timeframe}_{max_hold_hours}h",
                    held_hours=round(held.total_seconds() / 3600.0, 2),
                )
            ]

    actions: list[RiskAction] = []

    # 2. Breakeven move (idempotent).
    if not risk_state.get("breakeven_moved"):
        unrealized_r = compute_unrealized_r(
            setup.side, setup.entry_px, setup.stop_loss_px, close
        )
        if unrealized_r >= move_to_be_after_r:
            actions.append(
                BreakevenAction(
                    new_sl=setup.entry_px,
                    unrealized_r=round(unrealized_r, 3),
                )
            )
            # The BE move updates the SL we'll use to evaluate trailing below.
            effective_sl = setup.entry_px
        else:
            effective_sl = setup.stop_loss_px
    else:
        effective_sl = setup.stop_loss_px

    # 3. Trailing stop (only after TP1 + ATR available).
    if atr_value is not None and atr_value > 0 and _is_tp1_hit(setup):
        offset = atr_value * trailing_atr_multiple
        if setup.side == "long":
            candidate_sl = close - offset
            # Trailing only ratchets the stop UP (never widens risk).
            if candidate_sl > effective_sl:
                actions.append(
                    TrailingAction(
                        new_sl=candidate_sl,
                        atr_value=atr_value,
                        candidate_offset=offset,
                    )
                )
        else:  # short
            candidate_sl = close + offset
            if candidate_sl < effective_sl:
                actions.append(
                    TrailingAction(
                        new_sl=candidate_sl,
                        atr_value=atr_value,
                        candidate_offset=offset,
                    )
                )

    return actions


# -----------------------------------------------------------------------------
# DB application
# -----------------------------------------------------------------------------


async def apply_risk_action_to_db(
    session: AsyncSession,
    *,
    setup_id: str,
    action: RiskAction,
    candle_ts: datetime,
) -> None:
    """Persists ONE action: updates `stop_loss_px` + `risk_state` on
    `journal_trades` and writes the audit `setup_event`. Callers wrap multiple
    actions in `async with session_scope()` so the whole tick is atomic per setup.
    """
    if isinstance(action, BreakevenAction):
        risk_actions_total.labels(action="be_moved").inc()
        await session.execute(
            text(
                """
                UPDATE journal_trades
                SET stop_loss_px = :sl,
                    risk_state = COALESCE(risk_state, '{}'::jsonb)
                                || jsonb_build_object(
                                       'breakeven_moved', true,
                                       'breakeven_moved_at', :ts_iso,
                                       'breakeven_unrealized_r', :ur
                                   ),
                    updated_at = now()
                WHERE id = CAST(:tid AS uuid)
                """
            ),
            {
                "sl": action.new_sl,
                "ts_iso": candle_ts.isoformat(),
                "ur": action.unrealized_r,
                "tid": setup_id,
            },
        )
        await _insert_event(
            session,
            setup_id=setup_id,
            event="be_moved",
            candle_ts=candle_ts,
            payload={
                "new_sl": action.new_sl,
                "unrealized_r": action.unrealized_r,
            },
        )
        return

    if isinstance(action, TrailingAction):
        risk_actions_total.labels(action="trailing_updated").inc()
        await session.execute(
            text(
                """
                UPDATE journal_trades
                SET stop_loss_px = :sl,
                    risk_state = COALESCE(risk_state, '{}'::jsonb)
                                || jsonb_build_object(
                                       'trailing_active', true,
                                       'trailing_sl', :sl,
                                       'trailing_updated_at', :ts_iso,
                                       'trailing_atr', :atr,
                                       'trailing_offset', :off
                                   ),
                    updated_at = now()
                WHERE id = CAST(:tid AS uuid)
                """
            ),
            {
                "sl": action.new_sl,
                "ts_iso": candle_ts.isoformat(),
                "atr": action.atr_value,
                "off": action.candidate_offset,
                "tid": setup_id,
            },
        )
        await _insert_event(
            session,
            setup_id=setup_id,
            event="trailing_updated",
            candle_ts=candle_ts,
            payload={
                "new_sl": action.new_sl,
                "atr": action.atr_value,
                "offset": action.candidate_offset,
            },
        )
        return

    if isinstance(action, TimeStopAction):
        risk_actions_total.labels(action="time_stopped").inc()
        # Close the setup at the current candle's close price. Mark
        # risk_state.time_stopped so future ticks short-circuit (defensive —
        # the status='closed' guard would already skip it).
        await session.execute(
            text(
                """
                UPDATE journal_trades
                SET status = 'closed',
                    exit_px = :exit_px,
                    closed_at = :ts,
                    risk_state = COALESCE(risk_state, '{}'::jsonb)
                                || jsonb_build_object(
                                       'time_stopped', true,
                                       'time_stopped_at', :ts_iso,
                                       'time_stop_reason', :reason
                                   ),
                    updated_at = now()
                WHERE id = CAST(:tid AS uuid)
                  AND status = 'active'
                """
            ),
            {
                "exit_px": action.exit_px,
                "ts": candle_ts,
                "ts_iso": candle_ts.isoformat(),
                "reason": action.reason,
                "tid": setup_id,
            },
        )
        await _insert_event(
            session,
            setup_id=setup_id,
            event="time_stopped",
            candle_ts=candle_ts,
            payload={
                "exit_px": action.exit_px,
                "reason": action.reason,
                "held_hours": action.held_hours,
            },
        )
        return


async def _insert_event(
    session: AsyncSession,
    *,
    setup_id: str,
    event: Literal["be_moved", "trailing_updated", "time_stopped"],
    candle_ts: datetime,
    payload: dict[str, Any],
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO setup_events (trade_id, event, candle_ts, payload)
            VALUES (CAST(:tid AS uuid), :event, :ts, CAST(:payload AS jsonb))
            """
        ),
        {
            "tid": setup_id,
            "event": event,
            "ts": candle_ts,
            "payload": json.dumps(payload),
        },
    )


# -----------------------------------------------------------------------------
# ATR fetch helper (used by setup_runtime to feed compute_risk_actions)
# -----------------------------------------------------------------------------


async def fetch_atr_for_trailing(
    session: AsyncSession,
    *,
    symbol: str,
    timeframe: str,
    candle_ts: datetime,
    length: int = 14,
) -> float | None:
    """Fetch the last ATR(14) value for trailing. Returns None when there are
    fewer candles than the warm-up window (Wilder needs `length` rows of TR
    accumulated before producing a non-null value)."""
    rows = await fetch_range(
        session,
        exchange=EXCHANGE_NAME,
        symbol=symbol,
        timeframe=timeframe,
        until=candle_ts,
        limit=length + 20,  # extra rows for stable Wilder smoothing
    )
    # Wilder smoothing needs at least `length` closed bars after the first
    # one where TR is computable; <length is too thin to trust the value.
    if len(rows) < length:
        return None
    df = pl.DataFrame(
        {
            "h": [float(r.h) for r in rows],
            "l": [float(r.l) for r in rows],
            "c": [float(r.c) for r in rows],
        }
    )
    result = compute_atr_indicator(df.lazy(), length=length).collect()
    col_name = f"atr_{length}"
    if col_name not in result.columns:
        return None
    last_val = result[col_name][-1]
    if last_val is None:
        return None
    try:
        out = float(last_val)
    except (TypeError, ValueError):
        return None
    if out <= 0:
        return None
    return out
