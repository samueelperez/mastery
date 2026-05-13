"""Deterministic risk gates for setup proposals.

The blueprint principle is that pre-trade safety must NOT live inside the
LLM's reasoning loop — the agent could rationalise its way around a soft
prompt rule, but it cannot rationalise its way around a Python function
that returns ``passed=False``. Every hard gate listed in
``docs/cerebro1/CLAUDE.md::<money_and_risk_constants>`` lives here as a
pure function over (idea, settings) or (system_state, settings), and the
runtime evaluates them BEFORE persisting a setup.

Two flavours:

- **Idea-input gates** — pure functions over a ``TradeIdea`` + ``Settings``
  (R:R, max leverage, expectancy LCB).
- **System-state gates** — query DB / portfolio state, then evaluate
  (gross leverage, daily loss, drawdown, news blackout, cooldown streaks).

The orchestrator (:func:`policy.evaluate`) returns a :class:`GateReport`
that aggregates all gate outcomes. Hard failures block the setup; soft
failures degrade confidence and surface in ``risk_notes``.
"""

from app.risk.gates import (
    GateOutcome,
    GateReport,
    GateSeverity,
    daily_loss_gate,
    max_drawdown_gate,
    max_gross_leverage_gate,
    max_leverage_gate,
    min_expectancy_lcb_gate,
    min_factor_lcb_gate,
    min_rr_gate,
)
from app.risk.policy import evaluate_idea_input_gates

__all__ = [
    "GateOutcome",
    "GateReport",
    "GateSeverity",
    "daily_loss_gate",
    "evaluate_idea_input_gates",
    "max_drawdown_gate",
    "max_gross_leverage_gate",
    "max_leverage_gate",
    "min_expectancy_lcb_gate",
    "min_factor_lcb_gate",
    "min_rr_gate",
]
