"""B.3 Bias cooldown — pause the scout after consecutive losses.

`should_pause_scout(user_id, symbol)` returns whether the Scout (C.1) should
defer dispatching new proposals to the agent for either:

- the specific symbol (after ≥2 consecutive SL closes on it within
  `consec_window_symbol_h`, default 4h), OR
- ALL symbols globally (after ≥3 consecutive SL closes across any symbols
  within `consec_window_global_h`, default 8h).

The window measures from `now()` back, looking at trades closed within
that window. "Consecutive" means contiguous SL closes in time order — a
TP in between resets the streak.

Returned `ends_at` is the soonest time the scout may resume. If the symbol
trigger fired, returns the per-symbol pause (default 2h). If the global
trigger fired (regardless of symbol), returns the global pause (default 6h).
Both can fire at once; the caller respects the more restrictive (later) of
the two by computing both verdicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# -----------------------------------------------------------------------------
# Defaults — tunable in `Settings` later; B.3 keeps them as constants so the
# logic is self-contained and easy to unit-test.
# -----------------------------------------------------------------------------

CONSEC_WINDOW_SYMBOL_H = 4
CONSEC_LOSSES_SYMBOL = 2
PAUSE_SYMBOL_H = 2

CONSEC_WINDOW_GLOBAL_H = 8
CONSEC_LOSSES_GLOBAL = 3
PAUSE_GLOBAL_H = 6


@dataclass(frozen=True)
class CooldownVerdict:
    """Output of `should_pause_scout`. `paused=False` is the green-light state."""

    paused: bool
    reason: str
    ends_at: datetime | None
    consecutive_losses: int
    scope: str  # "symbol" | "global" | "none"


def evaluate_streak(
    *,
    closures: list[tuple[datetime, float, str]],
    now: datetime,
    window_hours: int,
    min_consecutive_losses: int,
) -> int:
    """Given recent closures sorted MOST-RECENT-FIRST (closed_at, r_multiple,
    symbol_or_marker), count the number of CONSECUTIVE losses (r_multiple<=0)
    starting from the most recent closure within the lookback window.

    Returns the count of consecutive losses; the caller checks against
    `min_consecutive_losses` to decide if the streak qualifies.

    A non-loss (r_multiple>0) or a closure outside the window breaks the streak.

    Pure function — no DB access. Lets us unit-test the logic with synthetic
    closure lists.
    """
    cutoff = now - timedelta(hours=window_hours)
    streak = 0
    for closed_at, r_mult, _ in closures:
        if closed_at < cutoff:
            break  # outside window, streak ends
        if r_mult is None or r_mult <= 0:
            streak += 1
            if streak >= min_consecutive_losses:
                # We can early-exit but we want the FULL count for telemetry.
                continue
        else:
            break  # win/breakeven breaks the streak
    return streak


def evaluate_cooldown_verdict(
    *,
    symbol_closures: list[tuple[datetime, float, str]],
    global_closures: list[tuple[datetime, float, str]],
    now: datetime,
) -> CooldownVerdict:
    """Pure dispatcher: combines symbol-scoped and global streak checks
    and returns the MOST restrictive verdict. The actual closures come from
    `_fetch_recent_closures` (impure).

    Order of priority when both fire:
    - global pause wins (longer window, more conservative). The verdict's
      `ends_at` reflects the global pause endpoint.
    """
    sym_streak = evaluate_streak(
        closures=symbol_closures,
        now=now,
        window_hours=CONSEC_WINDOW_SYMBOL_H,
        min_consecutive_losses=CONSEC_LOSSES_SYMBOL,
    )
    glb_streak = evaluate_streak(
        closures=global_closures,
        now=now,
        window_hours=CONSEC_WINDOW_GLOBAL_H,
        min_consecutive_losses=CONSEC_LOSSES_GLOBAL,
    )

    global_fired = glb_streak >= CONSEC_LOSSES_GLOBAL
    symbol_fired = sym_streak >= CONSEC_LOSSES_SYMBOL

    if global_fired:
        return CooldownVerdict(
            paused=True,
            reason=(
                f"{glb_streak} SL consecutivos globales en últimas "
                f"{CONSEC_WINDOW_GLOBAL_H}h"
            ),
            ends_at=now + timedelta(hours=PAUSE_GLOBAL_H),
            consecutive_losses=glb_streak,
            scope="global",
        )
    if symbol_fired:
        return CooldownVerdict(
            paused=True,
            reason=(
                f"{sym_streak} SL consecutivos en este símbolo en últimas "
                f"{CONSEC_WINDOW_SYMBOL_H}h"
            ),
            ends_at=now + timedelta(hours=PAUSE_SYMBOL_H),
            consecutive_losses=sym_streak,
            scope="symbol",
        )
    return CooldownVerdict(
        paused=False,
        reason="no_streak",
        ends_at=None,
        consecutive_losses=max(sym_streak, glb_streak),
        scope="none",
    )


# -----------------------------------------------------------------------------
# DB-bound entry point
# -----------------------------------------------------------------------------


async def _fetch_recent_closures(
    session: AsyncSession,
    *,
    user_id: str,
    symbol: str | None,
    lookback_hours: int,
) -> list[tuple[datetime, float, str]]:
    """Returns most-recent-first list of (closed_at, r_multiple, symbol) for
    a user. If `symbol` is provided, filters to that symbol; otherwise global.

    We grab the SETUP-DERIVED trades only (`source IN ('agent_proposal',
    'scout_proposal')`) so manual/log trades don't count toward cooldowns
    (they're often replay/test rows). Both bot-originated sources DO count
    because the cooldown's purpose is "the BOT has been losing recently,
    pause it" — scout losses are exactly what should fire it.
    Closures with `r_multiple IS NULL` are still returned and counted
    as losses by `evaluate_streak`.
    """
    sql = """
        SELECT closed_at, r_multiple, symbol
        FROM journal_trades
        WHERE user_id = :uid
          AND status = 'closed'
          AND source IN ('agent_proposal', 'scout_proposal')
          AND closed_at IS NOT NULL
          AND closed_at >= now() - make_interval(hours => :hours)
    """
    params: dict[str, object] = {"uid": user_id, "hours": lookback_hours}
    if symbol is not None:
        sql += " AND symbol = :sym"
        params["sym"] = symbol.upper()
    sql += " ORDER BY closed_at DESC"

    rows = (await session.execute(text(sql), params)).all()
    out: list[tuple[datetime, float, str]] = []
    for closed_at, r_mult, sym in rows:
        if closed_at is None:
            continue
        r_value = float(r_mult) if r_mult is not None else 0.0
        out.append((closed_at, r_value, sym))
    return out


async def should_pause_scout(
    session: AsyncSession,
    *,
    user_id: str,
    symbol: str,
) -> CooldownVerdict:
    """Main entry point used by C.1 ScoutDispatcher before invoking the agent.

    Two DB queries: one for the per-symbol window (shorter lookback) and one
    global (longer lookback). `evaluate_cooldown_verdict` picks the more
    restrictive verdict. Idempotent and side-effect free.
    """
    now = datetime.now(tz=UTC)
    sym_closures = await _fetch_recent_closures(
        session, user_id=user_id, symbol=symbol, lookback_hours=CONSEC_WINDOW_SYMBOL_H,
    )
    glb_closures = await _fetch_recent_closures(
        session, user_id=user_id, symbol=None, lookback_hours=CONSEC_WINDOW_GLOBAL_H,
    )
    return evaluate_cooldown_verdict(
        symbol_closures=sym_closures,
        global_closures=glb_closures,
        now=now,
    )
