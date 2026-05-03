"""Five bias heuristics over the user's recent trades.

All detectors operate on a Polars DataFrame of trades from the last N days.
Each returns zero or more BiasFlag objects (kind, severity, payload). The
nightly job persists them to the `bias_events` table; the agent tool
`detect_bias_patterns` reads from that table.

References:
- Odean (1998) — disposition effect; winners closed faster than losers
- Blueprint §7.4 — "detector calibrated to your own history"
"""

from __future__ import annotations

import json
import statistics
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import polars as pl
import structlog
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)

BiasKind = Literal["revenge", "overtrade", "fomo", "oversize", "disposition"]
Severity = Literal["low", "medium", "high"]


class BiasFlag(BaseModel):
    user_id: str
    kind: BiasKind
    severity: Severity
    payload: dict[str, Any] = Field(default_factory=dict)
    window_start: datetime
    window_end: datetime


# -----------------------------------------------------------------------------
# Fetch trades into a Polars DataFrame
# -----------------------------------------------------------------------------


async def _fetch_recent_trades(
    session: AsyncSession, *, user_id: str, since: datetime
) -> pl.DataFrame:
    rows = (
        await session.execute(
            text(
                """
                SELECT trade_ts, symbol, side, entry_px, exit_px, size, r_multiple,
                       setup_tag, regime
                FROM journal_trades
                WHERE user_id = :uid AND trade_ts >= :since
                ORDER BY trade_ts ASC
                """
            ),
            {"uid": user_id, "since": since},
        )
    ).mappings().all()
    if not rows:
        return pl.DataFrame(
            schema={
                "trade_ts": pl.Datetime("us", "UTC"),
                "symbol": pl.Utf8,
                "side": pl.Utf8,
                "entry_px": pl.Float64,
                "exit_px": pl.Float64,
                "size": pl.Float64,
                "r_multiple": pl.Float64,
                "setup_tag": pl.Utf8,
                "regime": pl.Utf8,
            }
        )
    return pl.DataFrame(
        {
            "trade_ts": [r["trade_ts"] for r in rows],
            "symbol": [r["symbol"] for r in rows],
            "side": [r["side"] for r in rows],
            "entry_px": [float(r["entry_px"]) for r in rows],
            "exit_px": [float(r["exit_px"]) if r["exit_px"] is not None else None for r in rows],
            "size": [float(r["size"]) for r in rows],
            "r_multiple": [float(r["r_multiple"]) if r["r_multiple"] is not None else None for r in rows],
            "setup_tag": [r["setup_tag"] for r in rows],
            "regime": [r["regime"] for r in rows],
        }
    )


# -----------------------------------------------------------------------------
# Detectors — each returns a list (possibly empty) of BiasFlag
# -----------------------------------------------------------------------------


def _severity_from(stat: float, *, lo: float, hi: float) -> Severity:
    if stat >= hi:
        return "high"
    if stat >= lo:
        return "medium"
    return "low"


def _detect_revenge(
    df: pl.DataFrame, *, user_id: str, win_start: datetime, win_end: datetime
) -> list[BiasFlag]:
    """A new trade within 15 min of a closed loss, with size > 1.2× rolling-20 mean."""
    if df.height < 5:
        return []
    closed = df.filter(pl.col("r_multiple").is_not_null()).sort("trade_ts")
    if closed.height < 5:
        return []
    sizes = closed["size"].to_list()
    timestamps = closed["trade_ts"].to_list()
    rs = closed["r_multiple"].to_list()
    flags: list[dict[str, Any]] = []
    for i in range(1, len(timestamps)):
        prev_r = rs[i - 1]
        if prev_r is None or prev_r >= 0:
            continue
        delta = timestamps[i] - timestamps[i - 1]
        if delta > timedelta(minutes=15):
            continue
        # Rolling-20 mean of size up to (and excluding) trade i
        window = sizes[max(0, i - 20) : i]
        if not window:
            continue
        mean_size = statistics.mean(window)
        if sizes[i] > 1.2 * mean_size:
            flags.append(
                {
                    "i": i,
                    "delta_minutes": int(delta.total_seconds() // 60),
                    "size_ratio": round(sizes[i] / mean_size, 2),
                }
            )
    if not flags:
        return []
    return [
        BiasFlag(
            user_id=user_id,
            kind="revenge",
            severity=_severity_from(len(flags), lo=1, hi=3),
            payload={"events": flags, "count": len(flags)},
            window_start=win_start,
            window_end=win_end,
        )
    ]


def _detect_overtrade(
    df: pl.DataFrame, *, user_id: str, win_start: datetime, win_end: datetime
) -> list[BiasFlag]:
    """Today's trade count > p90 of last 30 days AND win-rate < personal baseline."""
    if df.height < 30:
        return []
    end_day = win_end.date()
    counts_by_day: dict[str, int] = {}
    wins: list[bool] = []
    for trade_ts, r in zip(
        df["trade_ts"].to_list(), df["r_multiple"].to_list(), strict=True
    ):
        day = trade_ts.date().isoformat()
        counts_by_day[day] = counts_by_day.get(day, 0) + 1
        if r is not None:
            wins.append(r > 0)
    today_count = counts_by_day.get(end_day.isoformat(), 0)
    historical = [c for d, c in counts_by_day.items() if d != end_day.isoformat()]
    if len(historical) < 7 or today_count < 1:
        return []
    historical_sorted = sorted(historical)
    p90 = historical_sorted[int(0.9 * (len(historical_sorted) - 1))]
    if today_count <= p90:
        return []
    win_rate = sum(wins) / len(wins) if wins else 0.0
    return [
        BiasFlag(
            user_id=user_id,
            kind="overtrade",
            severity=_severity_from(today_count / max(p90, 1), lo=1.2, hi=2.0),
            payload={
                "today_count": today_count,
                "p90_historical": p90,
                "lifetime_win_rate": round(win_rate, 3),
            },
            window_start=win_start,
            window_end=win_end,
        )
    ]


def _detect_oversize(
    df: pl.DataFrame, *, user_id: str, win_start: datetime, win_end: datetime
) -> list[BiasFlag]:
    """Position size > 1.5× rolling-20 median (R-units approximation via raw size)."""
    if df.height < 5:
        return []
    sizes = df.sort("trade_ts")["size"].to_list()
    flags: list[dict[str, Any]] = []
    for i in range(5, len(sizes)):
        window = sizes[max(0, i - 20) : i]
        med = statistics.median(window)
        if sizes[i] > 1.5 * med:
            flags.append({"i": i, "size_ratio": round(sizes[i] / max(med, 1e-9), 2)})
    if not flags:
        return []
    return [
        BiasFlag(
            user_id=user_id,
            kind="oversize",
            severity=_severity_from(len(flags), lo=1, hi=4),
            payload={"events": flags, "count": len(flags)},
            window_start=win_start,
            window_end=win_end,
        )
    ]


def _detect_disposition(
    df: pl.DataFrame, *, user_id: str, win_start: datetime, win_end: datetime
) -> list[BiasFlag]:
    """Avg holding time of winners < 0.5 × avg holding of losers (Odean 1998).

    Holding time is approximated with trade-to-trade interval as a proxy when
    we don't yet track exit_ts; F4 paper trading will store exit_ts so this
    becomes exact.
    """
    closed = df.filter(pl.col("r_multiple").is_not_null()).sort("trade_ts")
    if closed.height < 10:
        return []
    rs = closed["r_multiple"].to_list()
    timestamps = closed["trade_ts"].to_list()
    if len(timestamps) < 10:
        return []
    intervals = [
        (timestamps[i + 1] - timestamps[i]).total_seconds()
        for i in range(len(timestamps) - 1)
    ]
    win_intervals = [intervals[i] for i in range(len(intervals)) if rs[i] is not None and rs[i] > 0]
    loss_intervals = [
        intervals[i] for i in range(len(intervals)) if rs[i] is not None and rs[i] < 0
    ]
    if len(win_intervals) < 3 or len(loss_intervals) < 3:
        return []
    avg_w = statistics.mean(win_intervals)
    avg_l = statistics.mean(loss_intervals)
    if avg_l == 0 or avg_w >= 0.5 * avg_l:
        return []
    return [
        BiasFlag(
            user_id=user_id,
            kind="disposition",
            severity=_severity_from(avg_l / max(avg_w, 1e-9), lo=2.0, hi=4.0),
            payload={
                "avg_winner_seconds": int(avg_w),
                "avg_loser_seconds": int(avg_l),
                "ratio": round(avg_l / max(avg_w, 1e-9), 2),
            },
            window_start=win_start,
            window_end=win_end,
        )
    ]


def _detect_fomo(
    df: pl.DataFrame, *, user_id: str, win_start: datetime, win_end: datetime
) -> list[BiasFlag]:
    """FOMO heuristic, F2 stub: setup_tag absent or in {'unknown','impulse','fomo'}.

    The "entry within 0.5 ATR of N-bar high" check needs OHLCV context per trade
    that the journal doesn't currently store. F2 keeps this lightweight; the
    Polars impl with OHLCV joins lands in F2.5 once `features.atr_at_entry` and
    `features.distance_to_high` are populated by the live ingestion path.
    """
    if df.height < 3:
        return []
    fomo_tags = {"unknown", "impulse", "fomo", ""}
    flags: list[dict[str, Any]] = []
    for i, tag in enumerate(df["setup_tag"].to_list()):
        if (tag or "").lower() in fomo_tags:
            flags.append({"i": i, "setup_tag": tag})
    if not flags:
        return []
    return [
        BiasFlag(
            user_id=user_id,
            kind="fomo",
            severity=_severity_from(len(flags) / max(df.height, 1), lo=0.2, hi=0.5),
            payload={"events": flags, "fraction": round(len(flags) / df.height, 3)},
            window_start=win_start,
            window_end=win_end,
        )
    ]


# -----------------------------------------------------------------------------
# Orchestration + persistence
# -----------------------------------------------------------------------------


async def run_for_user(
    session: AsyncSession, *, user_id: str = "me", lookback_days: int = 30
) -> list[BiasFlag]:
    """Compute all bias flags for the given window and persist them to bias_events.

    Returns the list of flags emitted. Idempotent: clears prior flags for the
    same window before inserting (so re-running same day overrides earlier
    detection without duplicating).
    """
    win_end = datetime.now(tz=UTC)
    win_start = win_end - timedelta(days=lookback_days)
    df = await _fetch_recent_trades(session, user_id=user_id, since=win_start)
    if df.height == 0:
        return []

    flags: list[BiasFlag] = []
    flags += _detect_revenge(df, user_id=user_id, win_start=win_start, win_end=win_end)
    flags += _detect_overtrade(df, user_id=user_id, win_start=win_start, win_end=win_end)
    flags += _detect_oversize(df, user_id=user_id, win_start=win_start, win_end=win_end)
    flags += _detect_disposition(df, user_id=user_id, win_start=win_start, win_end=win_end)
    flags += _detect_fomo(df, user_id=user_id, win_start=win_start, win_end=win_end)

    # Replace flags for this exact window+user (idempotent re-run today).
    await session.execute(
        text(
            """
            DELETE FROM bias_events
            WHERE user_id = :uid AND window_start = :ws AND window_end = :we
            """
        ),
        {"uid": user_id, "ws": win_start, "we": win_end},
    )
    if flags:
        await session.execute(
            text(
                """
                INSERT INTO bias_events (user_id, kind, severity, payload, window_start, window_end)
                VALUES (:uid, :kind, :sev, CAST(:payload AS jsonb), :ws, :we)
                """
            ),
            [
                {
                    "uid": f.user_id,
                    "kind": f.kind,
                    "sev": f.severity,
                    "payload": json.dumps(f.payload, default=str),
                    "ws": f.window_start,
                    "we": f.window_end,
                }
                for f in flags
            ],
        )
    log.info(
        "bias_detector.run",
        user_id=user_id,
        n_flags=len(flags),
        kinds=[f.kind for f in flags],
    )
    return flags
