"""Pure-function rule evaluator.

Given a `RuleSpec` and an enriched panel (Polars DataFrame with OHLCV +
indicator columns), return whether the rule fires on the latest closed bar.

No DB, no I/O, no time. Tests live in `tests/alerts/test_evaluator.py`.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from app.alerts.dsl import Condition, Operator, RuleSpec


def _read(panel: pl.DataFrame, name: str, *, row: int = -1) -> float | None:
    """Look up a column at row index `row` (default -1 = last). Returns None
    when the column is missing (uncomputed indicator) or the cell is null."""
    if name not in panel.columns:
        return None
    n = panel.height
    if n == 0:
        return None
    idx = row if row >= 0 else n + row
    if idx < 0 or idx >= n:
        return None
    val = panel[name][idx]
    if val is None:
        return None
    return float(val)


def _resolve_right(panel: pl.DataFrame, right: float | str, *, row: int = -1) -> float | None:
    if isinstance(right, (int, float)):
        return float(right)
    return _read(panel, right, row=row)


def _apply_op(op: Operator, lhs: float, rhs: float) -> bool:
    match op:
        case "<":
            return lhs < rhs
        case "<=":
            return lhs <= rhs
        case "==":
            return lhs == rhs
        case ">=":
            return lhs >= rhs
        case ">":
            return lhs > rhs
        case _:
            return False  # cross_* handled separately


def _check_cross(
    panel: pl.DataFrame, c: Condition
) -> bool:
    """`cross_above`: previous bar lhs < rhs AND current bar lhs >= rhs.
    `cross_below`: previous bar lhs > rhs AND current bar lhs <= rhs."""
    if panel.height < 2:
        return False
    prev_l = _read(panel, c.left, row=-2)
    prev_r = _resolve_right(panel, c.right, row=-2)
    curr_l = _read(panel, c.left, row=-1)
    curr_r = _resolve_right(panel, c.right, row=-1)
    if None in (prev_l, prev_r, curr_l, curr_r):
        return False
    if c.op == "cross_above":
        return prev_l < prev_r and curr_l >= curr_r  # type: ignore[operator]
    if c.op == "cross_below":
        return prev_l > prev_r and curr_l <= curr_r  # type: ignore[operator]
    return False


def evaluate_condition(panel: pl.DataFrame, c: Condition) -> bool:
    if c.op in ("cross_above", "cross_below"):
        return _check_cross(panel, c)
    lhs = _read(panel, c.left)
    rhs = _resolve_right(panel, c.right)
    if lhs is None or rhs is None:
        return False
    return _apply_op(c.op, lhs, rhs)


def evaluate_rule(spec: RuleSpec, panel: pl.DataFrame) -> bool:
    """Evaluate every condition; combine via `spec.logic` (all|any)."""
    if not spec.conditions:
        return False
    results = [evaluate_condition(panel, c) for c in spec.conditions]
    return all(results) if spec.logic == "all" else any(results)


def build_snapshot(spec: RuleSpec, panel: pl.DataFrame) -> dict[str, Any]:
    """Compact, JSON-friendly view of the values that triggered the rule.

    Used as the `alert_events.snapshot` payload so the user (and the agent on
    citation) can see exactly what numbers fired, without re-fetching.
    """
    if panel.height == 0:
        return {"reason": "empty panel"}
    last = {
        col: (
            None
            if panel[col][-1] is None
            else float(panel[col][-1])
            if panel[col].dtype.is_numeric()
            else str(panel[col][-1])
        )
        for col in panel.columns
        if col != "ts"
    }
    return {
        "ts": panel["ts"][-1].isoformat() if "ts" in panel.columns else None,
        "symbol": spec.symbol,
        "timeframe": spec.timeframe,
        "values": last,
        "matched_conditions": [
            {
                "left": c.left,
                "op": c.op,
                "right": c.right,
            }
            for c in spec.conditions
            if evaluate_condition(panel, c)
        ],
    }
