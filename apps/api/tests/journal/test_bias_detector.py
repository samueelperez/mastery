"""Test bias detector heuristics on synthetic Polars DataFrames.

These tests bypass DB I/O — we feed a hand-built DataFrame straight into the
private detectors (re-exposed via _detect_*).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl

from app.journal.bias_detector import (
    _detect_disposition,
    _detect_oversize,
    _detect_overtrade,
    _detect_revenge,
)

WIN_END = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)  # noon, so hour offsets stay on same date
WIN_START = WIN_END - timedelta(days=30)


def _df(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "trade_ts": [r["trade_ts"] for r in rows],
            "symbol": [r.get("symbol", "BTCUSDT") for r in rows],
            "side": [r.get("side", "long") for r in rows],
            "entry_px": [r.get("entry_px", 100.0) for r in rows],
            "exit_px": [r.get("exit_px") for r in rows],
            "size": [r["size"] for r in rows],
            "r_multiple": [r.get("r_multiple") for r in rows],
            "setup_tag": [r.get("setup_tag", "breakout_4h") for r in rows],
            "regime": [r.get("regime", "trending_up") for r in rows],
        }
    )


def test_revenge_flagged_when_oversized_trade_after_loss_within_15min() -> None:
    base_ts = WIN_END - timedelta(days=2)
    # 5 baseline trades sized ~1.0
    baseline = [
        {"trade_ts": base_ts - timedelta(hours=h), "size": 1.0, "r_multiple": 0.5}
        for h in range(20, 5, -3)
    ]
    # A loss
    loss = {"trade_ts": base_ts, "size": 1.0, "r_multiple": -1.0}
    # Revenge trade: 5 min later, size 2.0 (2x baseline mean)
    revenge = {
        "trade_ts": base_ts + timedelta(minutes=5),
        "size": 2.0,
        "r_multiple": -0.5,
    }
    df = _df([*baseline, loss, revenge])
    flags = _detect_revenge(df, user_id="me", win_start=WIN_START, win_end=WIN_END)
    assert len(flags) == 1
    assert flags[0].kind == "revenge"
    assert flags[0].payload["count"] >= 1


def test_revenge_NOT_flagged_when_size_normal() -> None:
    base_ts = WIN_END - timedelta(days=2)
    baseline = [
        {"trade_ts": base_ts - timedelta(hours=h), "size": 1.0, "r_multiple": 0.5}
        for h in range(20, 5, -3)
    ]
    loss = {"trade_ts": base_ts, "size": 1.0, "r_multiple": -1.0}
    not_revenge = {
        "trade_ts": base_ts + timedelta(minutes=5),
        "size": 1.0,  # same as baseline — not oversize
        "r_multiple": -0.2,
    }
    df = _df([*baseline, loss, not_revenge])
    flags = _detect_revenge(df, user_id="me", win_start=WIN_START, win_end=WIN_END)
    assert flags == []


def test_oversize_flagged() -> None:
    rows = [
        {"trade_ts": WIN_END - timedelta(hours=i), "size": 1.0, "r_multiple": 0.0}
        for i in range(30, 5, -1)
    ]
    rows.append({"trade_ts": WIN_END, "size": 5.0, "r_multiple": 0.0})  # 5x median
    df = _df(rows)
    flags = _detect_oversize(df, user_id="me", win_start=WIN_START, win_end=WIN_END)
    assert len(flags) == 1
    assert flags[0].kind == "oversize"


def test_disposition_flagged_when_winners_held_briefly() -> None:
    """Heuristic uses 'time until next trade' as a holding-time proxy. To
    trigger it, alternate loser-then-winner so each loser's interval = 1h
    (held the loser long), each winner's interval = 1 min (cut quickly).
    """
    rows: list[dict] = []
    ts = WIN_END - timedelta(days=2)
    for _ in range(15):
        rows.append({"trade_ts": ts, "size": 1.0, "r_multiple": -0.5})  # loser
        ts += timedelta(hours=1)  # held ~1h before next entry
        rows.append({"trade_ts": ts, "size": 1.0, "r_multiple": 0.5})  # winner
        ts += timedelta(minutes=1)  # cut ~1 min before next entry
    df = _df(rows).sort("trade_ts")
    flags = _detect_disposition(df, user_id="me", win_start=WIN_START, win_end=WIN_END)
    assert len(flags) == 1
    assert flags[0].kind == "disposition"


def test_overtrade_flagged_when_today_count_exceeds_p90() -> None:
    rows: list[dict] = []
    # 30 days of 1 trade each
    for d in range(30):
        rows.append(
            {
                "trade_ts": WIN_END - timedelta(days=30 - d),
                "size": 1.0,
                "r_multiple": 0.5 if d % 2 else -0.3,
            }
        )
    # Today: 8 trades all on the same calendar day (use minute offsets, not hours,
    # so they don't overflow into the previous day).
    for m in range(8):
        rows.append(
            {
                "trade_ts": WIN_END - timedelta(minutes=30 * m),
                "size": 1.0,
                "r_multiple": -0.5,
            }
        )
    df = _df(rows)
    flags = _detect_overtrade(df, user_id="me", win_start=WIN_START, win_end=WIN_END)
    assert len(flags) == 1
    assert flags[0].kind == "overtrade"
    assert flags[0].payload["today_count"] == 8
