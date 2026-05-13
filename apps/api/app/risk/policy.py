"""Policy layer — orchestrates the idea-input gates into a single verdict.

State-dependent gates (gross leverage, daily loss, drawdown, news blackout,
cooldown streaks) are NOT included here because they require DB fetches.
The runtime wiring (PR-RM-2) composes the full report by calling the
state fetchers and then the per-gate functions in ``gates.py``.
"""

from __future__ import annotations

from typing import Any

from app.risk.gates import (
    GateReport,
    max_leverage_gate,
    min_rr_gate,
)


def evaluate_idea_input_gates(*, idea: Any, settings: Any) -> GateReport:
    """Run every pure idea-only gate over the proposed ``TradeIdea``.

    Returns a :class:`GateReport`. ``report.passed`` is ``True`` when no
    hard gate failed — caller checks this and rejects the idea
    accordingly (typically by raising ``ModelRetry`` from the validator).
    """
    outcomes = [
        min_rr_gate(idea, settings),
        max_leverage_gate(idea, settings),
    ]
    return GateReport(outcomes=outcomes)
