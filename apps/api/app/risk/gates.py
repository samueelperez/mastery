"""Pure-function risk gates.

Each gate is a small function that takes a ``TradeIdea`` (or a portfolio
snapshot) plus the ``Settings`` and returns a :class:`GateOutcome`.

Design conventions:

- **Pure**: gates do not touch the DB or the network. State-dependent
  gates (gross leverage, daily loss, drawdown) accept their inputs as
  parameters so they remain testable without fixtures.
- **No side effects**: emitting metrics or logs is the caller's job.
- **Hard vs soft**: hard gates ``passed=False`` reject the setup. Soft
  gates ``passed=False`` degrade confidence + surface a warning; the
  setup still flows.
- **Defensive on inputs**: a gate that doesn't apply (e.g. R:R when the
  idea has no targets) returns ``passed=True`` with ``skipped=True`` so
  the orchestrator can distinguish "gate green" from "gate skipped".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

GateSeverity = Literal["hard", "soft"]


@dataclass(frozen=True)
class GateOutcome:
    """Result of one gate evaluation.

    Attributes:
        name: gate identifier (snake_case, stable — used as a metric label
            and as the key in :class:`GateReport`).
        passed: True when the gate did not fail. Note that ``skipped=True``
            also implies ``passed=True``.
        severity: ``hard`` means ``passed=False`` rejects the setup;
            ``soft`` means it only degrades confidence.
        reason: human-readable failure cause when ``passed=False``;
            ``None`` when the gate is green or skipped.
        skipped: True if the gate didn't apply to this input (e.g. R:R on
            an idea with no targets). Skipped gates report ``passed=True``
            so they don't accidentally block — but the orchestrator knows
            they weren't actually checked.
        metadata: per-gate diagnostic payload, optional. Used by tests and
            by the runtime for structured logging.
    """

    name: str
    passed: bool
    severity: GateSeverity
    reason: str | None = None
    skipped: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GateReport:
    """Aggregate outcome of a full gate pass."""

    outcomes: list[GateOutcome]

    @property
    def hard_failures(self) -> list[GateOutcome]:
        return [o for o in self.outcomes if not o.passed and o.severity == "hard"]

    @property
    def soft_failures(self) -> list[GateOutcome]:
        return [o for o in self.outcomes if not o.passed and o.severity == "soft"]

    @property
    def passed(self) -> bool:
        """True when no hard failure was recorded. Soft failures do not
        flip this — they are advisory."""
        return not self.hard_failures

    def reason_summary(self) -> str:
        """Human-readable summary of the failures (hard first, then soft)."""
        parts: list[str] = []
        for o in self.hard_failures:
            parts.append(f"[HARD] {o.name}: {o.reason}")
        for o in self.soft_failures:
            parts.append(f"[SOFT] {o.name}: {o.reason}")
        return "; ".join(parts) if parts else "all gates green"


# ---------------------------------------------------------------------------
# Idea-input gates — pure over (idea, settings)
# ---------------------------------------------------------------------------


def min_rr_gate(idea: Any, settings: Any) -> GateOutcome:
    """Reject when the reward/risk ratio falls below
    ``settings.min_rr_ratio`` + per-symbol slippage buffer.

    Hard gate. Skipped if the idea is not directional or lacks levels.
    """
    if (
        getattr(idea, "direction", None) not in ("long", "short")
        or getattr(idea, "entry", None) is None
        or getattr(idea, "stop_loss", None) is None
        or not getattr(idea, "targets", None)
    ):
        return GateOutcome("min_rr_ratio", True, "hard", skipped=True)

    entry = float(idea.entry)
    stop = float(idea.stop_loss)
    target0 = float(idea.targets[0].price)
    risk = abs(entry - stop)
    reward = abs(target0 - entry)
    if risk == 0:
        return GateOutcome(
            "min_rr_ratio",
            False,
            "hard",
            reason="stop_loss == entry (risk=0)",
            metadata={"risk": 0.0},
        )
    rr = reward / risk
    slippage = (
        settings.slippage_buffer_r(idea.symbol)
        if hasattr(settings, "slippage_buffer_r")
        else 0.0
    )
    threshold = float(settings.min_rr_ratio) + float(slippage)
    passed = rr >= threshold
    reason = (
        None
        if passed
        else f"R:R {rr:.2f} < {threshold:.2f} (base {settings.min_rr_ratio} + slippage {slippage})"
    )
    return GateOutcome(
        "min_rr_ratio",
        passed,
        "hard",
        reason=reason,
        metadata={"rr": rr, "threshold": threshold},
    )


def max_leverage_gate(idea: Any, settings: Any) -> GateOutcome:
    """Reject when ``idea.leverage_x`` exceeds
    ``settings.max_leverage_per_position``. Hard gate."""
    lev = getattr(idea, "leverage_x", None)
    if lev is None:
        return GateOutcome("max_leverage_per_position", True, "hard", skipped=True)
    lev_f = float(lev)
    cap = float(settings.max_leverage_per_position)
    passed = lev_f <= cap
    reason = (
        None if passed else f"leverage {lev_f:g}x exceeds per-position cap {cap:g}x"
    )
    return GateOutcome(
        "max_leverage_per_position",
        passed,
        "hard",
        reason=reason,
        metadata={"leverage": lev_f, "cap": cap},
    )


def max_gross_leverage_gate(
    *,
    current_gross_leverage: float,
    proposed_size_usd: float,
    proposed_leverage_x: float,
    equity_usd: float,
    settings: Any,
) -> GateOutcome:
    """Reject when adding the proposed setup would push portfolio gross
    leverage above ``settings.max_gross_leverage``.

    Hard gate. Caller computes ``current_gross_leverage`` and ``equity_usd``
    from the live portfolio (paper_balance + open positions). The check is
    ``(current_gross * equity + proposed_size * proposed_lev) / equity``.
    """
    if equity_usd <= 0:
        return GateOutcome(
            "max_gross_leverage",
            False,
            "hard",
            reason=f"equity_usd={equity_usd} (non-positive)",
            metadata={"equity_usd": equity_usd},
        )
    notional_after = (
        current_gross_leverage * equity_usd
        + proposed_size_usd * proposed_leverage_x
    )
    gross_after = notional_after / equity_usd
    cap = float(settings.max_gross_leverage)
    passed = gross_after <= cap
    reason = (
        None
        if passed
        else f"adding this setup pushes gross leverage to {gross_after:.2f}x (cap {cap:g}x)"
    )
    return GateOutcome(
        "max_gross_leverage",
        passed,
        "hard",
        reason=reason,
        metadata={"gross_after": gross_after, "cap": cap},
    )


def min_factor_lcb_gate(*, win_rate_lcb: float, settings: Any) -> GateOutcome:
    """Soft veto when the worst factor's Bayesian win-rate LCB is below
    ``settings.min_factor_lcb``. Soft → degrades confidence to 'low'."""
    threshold = float(settings.min_factor_lcb)
    passed = win_rate_lcb >= threshold
    reason = (
        None
        if passed
        else f"factor win_rate_lcb {win_rate_lcb:.2f} < {threshold:.2f}"
    )
    return GateOutcome(
        "min_factor_lcb",
        passed,
        "soft",
        reason=reason,
        metadata={"lcb": win_rate_lcb, "threshold": threshold},
    )


def min_expectancy_lcb_gate(*, expectancy_lcb_r: float, settings: Any) -> GateOutcome:
    """Hard veto when the factor-mix R-multiple expectancy LCB is below
    ``settings.min_expectancy_lcb_r``.

    Expectancy is the dollar-weighted reward — LCB < 0.25R means the lower
    bound of the expected R per trade is too thin to justify exposure.
    """
    threshold = float(settings.min_expectancy_lcb_r)
    passed = expectancy_lcb_r >= threshold
    reason = (
        None
        if passed
        else f"expectancy_lcb {expectancy_lcb_r:.2f}R < {threshold:.2f}R"
    )
    return GateOutcome(
        "min_expectancy_lcb_r",
        passed,
        "hard",
        reason=reason,
        metadata={"expectancy_lcb_r": expectancy_lcb_r, "threshold": threshold},
    )


# ---------------------------------------------------------------------------
# System-state gates — pure over snapshot input (caller fetches state)
# ---------------------------------------------------------------------------


def daily_loss_gate(
    *,
    realized_pnl_last_24h_usd: float,
    equity_usd: float,
    settings: Any,
) -> GateOutcome:
    """Reject when realized loss in the last 24h breaches
    ``settings.daily_loss_limit_pct`` of equity.

    Hard gate. Triggers a 24h freeze on new setups (the cooldown is
    enforced by the caller — this gate only reports the breach).
    """
    if equity_usd <= 0:
        return GateOutcome(
            "daily_loss_limit",
            False,
            "hard",
            reason=f"equity_usd={equity_usd} (non-positive)",
            metadata={"equity_usd": equity_usd},
        )
    pct = -realized_pnl_last_24h_usd / equity_usd * 100.0
    threshold = float(settings.daily_loss_limit_pct)
    passed = pct < threshold
    reason = (
        None
        if passed
        else f"24h loss {pct:.2f}% ≥ limit {threshold:.2f}% — 24h freeze required"
    )
    return GateOutcome(
        "daily_loss_limit",
        passed,
        "hard",
        reason=reason,
        metadata={"loss_pct": pct, "threshold": threshold},
    )


def max_drawdown_gate(
    *,
    current_equity_usd: float,
    high_watermark_usd: float,
    settings: Any,
) -> GateOutcome:
    """Reject when drawdown from high-watermark exceeds
    ``settings.max_drawdown_circuit_pct``.

    Hard gate. Breach requires manual unlock — the caller surfaces this
    to the operator and persists a circuit-break flag in Settings or DB.
    """
    if high_watermark_usd <= 0:
        return GateOutcome("max_drawdown_circuit", True, "hard", skipped=True)
    dd_pct = (high_watermark_usd - current_equity_usd) / high_watermark_usd * 100.0
    threshold = float(settings.max_drawdown_circuit_pct)
    passed = dd_pct < threshold
    reason = (
        None
        if passed
        else f"drawdown {dd_pct:.2f}% ≥ circuit {threshold:.2f}% — manual unlock required"
    )
    return GateOutcome(
        "max_drawdown_circuit",
        passed,
        "hard",
        reason=reason,
        metadata={"dd_pct": dd_pct, "threshold": threshold},
    )
