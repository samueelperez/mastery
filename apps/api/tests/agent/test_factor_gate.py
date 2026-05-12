"""A.2 — Factor stats progressive gate.

Two layers of tests:

1. Pure decisional core (``_apply_gate_to_rates`` + ``_factor_kind_and_keys``)
   — no DB needed, exercises the policy table directly.

2. Validator integration — monkeypatch ``evaluate_factor_gate`` to inject a
   crafted ``GateVerdict`` so we can assert the validator's downstream
   behavior (ModelRetry on hard veto, confidence downgrade on soft veto).
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
import structlog
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel

from app.agent.deps import AgentDeps
from app.agent.models import (
    Confluence,
    FactorBlock,
    GateVerdict,
    MarketRegime,
    ToolCitation,
    TradeIdea,
    TradeIdeaTarget,
)
from app.storage.factor_stats_repo import (
    FACTOR_GATE_HARD_LCB_THRESHOLD,
    FACTOR_GATE_HARD_MIN_N,
    FACTOR_GATE_SOFT_LCB_THRESHOLD,
    FACTOR_GATE_SOFT_MIN_N,
    FactorHitRate,
    _apply_gate_to_rates,
    _factor_kind_and_keys,
)

# ----------------------------------------------------------------------------
# Pure tests — _factor_kind_and_keys
# ----------------------------------------------------------------------------


def test_extract_deterministic_factors_above_threshold() -> None:
    snapshot = {
        "deterministic": {
            "by_tf": {
                "1h": {"ema_stack": 0.8, "rsi": 0.2, "score_total": 0.5},
                "4h": {"ema_stack": -0.6},
            }
        },
        "semantic_tags": [],
    }
    triples = _factor_kind_and_keys(snapshot)
    assert ("ema_stack", "1h", "deterministic") in triples
    assert ("ema_stack", "4h", "deterministic") in triples
    # rsi=0.2 is below PRESENT_THRESHOLD (0.4) → excluded
    assert ("rsi", "1h", "deterministic") not in triples
    # score_total is never a factor
    assert not any(name == "score_total" for name, _, _ in triples)


def test_extract_semantic_tags() -> None:
    snapshot = {
        "deterministic": {"by_tf": {}},
        "semantic_tags": ["lvn_support", "vwap_reclaim"],
    }
    triples = _factor_kind_and_keys(snapshot)
    assert ("lvn_support", None, "semantic") in triples
    assert ("vwap_reclaim", None, "semantic") in triples


def test_extract_handles_empty_snapshot() -> None:
    assert _factor_kind_and_keys({}) == []
    assert _factor_kind_and_keys({"deterministic": None, "semantic_tags": None}) == []


# ----------------------------------------------------------------------------
# Pure tests — _apply_gate_to_rates
# ----------------------------------------------------------------------------


def _make_rate(
    *,
    factor_name: str,
    factor_tf: str | None,
    factor_kind: str,
    n: int,
    lcb: float,
) -> FactorHitRate:
    return FactorHitRate(
        factor_name=factor_name,
        factor_tf=factor_tf,
        factor_kind=factor_kind,  # type: ignore[arg-type]
        n_trades=n,
        n_wins=int(n * lcb),  # not used by gate; coherent enough
        win_rate_mean=lcb + 0.05,
        win_rate_lcb=lcb,
        win_rate_ucb=min(lcb + 0.3, 1.0),
        avg_r=None,
        expectancy_r=None,
        last_closed_at=datetime.now(tz=UTC),
    )


def test_gate_advisory_below_n30() -> None:
    """n < 30 → advisory regardless of LCB."""
    triples = [("ema_stack", "1h", "deterministic")]
    rates = [
        _make_rate(
            factor_name="ema_stack",
            factor_tf="1h",
            factor_kind="deterministic",
            n=10,
            lcb=0.15,  # very weak
        )
    ]
    verdict = _apply_gate_to_rates(triples, rates)
    assert verdict.passed is True
    assert not verdict.blocking_factors
    assert not verdict.soft_veto_factors
    assert len(verdict.advisory_factors) == 1
    assert verdict.advisory_factors[0].severity == "advisory"


def test_gate_soft_veto_band() -> None:
    """30 ≤ n < 100 ∧ LCB < 35% → soft_veto."""
    triples = [("ema_stack", "1h", "deterministic")]
    rates = [
        _make_rate(
            factor_name="ema_stack",
            factor_tf="1h",
            factor_kind="deterministic",
            n=50,
            lcb=0.25,
        )
    ]
    verdict = _apply_gate_to_rates(triples, rates)
    assert verdict.passed is True  # soft_veto doesn't block
    assert len(verdict.soft_veto_factors) == 1
    assert verdict.soft_veto_factors[0].severity == "soft_veto"
    assert not verdict.blocking_factors


def test_gate_hard_veto() -> None:
    """n ≥ 100 ∧ LCB < 30% → hard_veto (passed=False)."""
    triples = [("lvn_support", None, "semantic")]
    rates = [
        _make_rate(
            factor_name="lvn_support",
            factor_tf=None,
            factor_kind="semantic",
            n=150,
            lcb=0.22,
        )
    ]
    verdict = _apply_gate_to_rates(triples, rates)
    assert verdict.passed is False
    assert len(verdict.blocking_factors) == 1
    assert verdict.blocking_factors[0].severity == "hard_veto"


def test_gate_healthy_factor_no_entry() -> None:
    """Factor with enough samples AND healthy LCB → no entry (passed=True,
    empty buckets)."""
    triples = [("ema_stack", "1h", "deterministic")]
    rates = [
        _make_rate(
            factor_name="ema_stack",
            factor_tf="1h",
            factor_kind="deterministic",
            n=200,
            lcb=0.55,
        )
    ]
    verdict = _apply_gate_to_rates(triples, rates)
    assert verdict.passed is True
    assert not verdict.blocking_factors
    assert not verdict.soft_veto_factors
    assert not verdict.advisory_factors


def test_gate_missing_history_becomes_advisory() -> None:
    """Factor present in setup but no prior outcomes (rates list empty) →
    advisory entry with n=0."""
    triples = [("vwap_reclaim", None, "semantic")]
    verdict = _apply_gate_to_rates(triples, rates=[])
    assert verdict.passed is True
    assert len(verdict.advisory_factors) == 1
    advisory = verdict.advisory_factors[0]
    assert advisory.n_trades == 0
    assert advisory.severity == "advisory"


def test_gate_thresholds_at_boundaries() -> None:
    """n exactly at 100 with LCB < 30% should hit hard_veto (≥ boundary)."""
    triples = [("ema_stack", "1h", "deterministic")]
    rates = [
        _make_rate(
            factor_name="ema_stack",
            factor_tf="1h",
            factor_kind="deterministic",
            n=FACTOR_GATE_HARD_MIN_N,
            lcb=FACTOR_GATE_HARD_LCB_THRESHOLD - 0.01,
        )
    ]
    verdict = _apply_gate_to_rates(triples, rates)
    assert verdict.passed is False

    # n=29 should NOT trigger soft_veto (below the soft min)
    rates2 = [
        _make_rate(
            factor_name="ema_stack",
            factor_tf="1h",
            factor_kind="deterministic",
            n=FACTOR_GATE_SOFT_MIN_N - 1,
            lcb=FACTOR_GATE_SOFT_LCB_THRESHOLD - 0.05,
        )
    ]
    verdict2 = _apply_gate_to_rates(triples, rates2)
    assert verdict2.passed is True
    assert not verdict2.soft_veto_factors
    assert len(verdict2.advisory_factors) == 1


def test_gate_mixed_blockers_and_soft() -> None:
    triples = [
        ("ema_stack", "1h", "deterministic"),
        ("lvn_support", None, "semantic"),
    ]
    rates = [
        # Hard veto
        _make_rate(
            factor_name="ema_stack",
            factor_tf="1h",
            factor_kind="deterministic",
            n=150,
            lcb=0.20,
        ),
        # Soft veto
        _make_rate(
            factor_name="lvn_support",
            factor_tf=None,
            factor_kind="semantic",
            n=50,
            lcb=0.30,
        ),
    ]
    verdict = _apply_gate_to_rates(triples, rates)
    assert verdict.passed is False  # any hard veto wins
    assert len(verdict.blocking_factors) == 1
    assert len(verdict.soft_veto_factors) == 1


# ----------------------------------------------------------------------------
# Validator integration tests
# ----------------------------------------------------------------------------


@dataclass
class _StubDeps:
    log: Any
    user_id: str
    session_factory: Any
    exchange: str = "binanceusdm"


@asynccontextmanager
async def _noop_session():
    yield None


def _multi_tf_confluence_payload(*, aggregate_bias: str = "bull") -> dict[str, Any]:
    return {
        "data": {
            "by_tf": [
                {
                    "timeframe": "1h",
                    "score_components": {
                        "ema_stack": 1.0,
                        "regime": 0.5,
                        "rsi": 0.3,
                        "volume": 0.4,
                        "distance_atr": 0.6,
                    },
                    "score_total": 0.58,
                }
            ],
            "aggregate_bias": aggregate_bias,
            "aggregate_agreement_pct": 75.0,
        },
        "provenance": {"source": "test", "warnings": []},
    }


def _market_structure_payload(*, current_close: float = 100.0) -> dict[str, Any]:
    return {
        "data": {
            "swing_highs": [],
            "swing_lows": [],
            "support": [],
            "resistance": [],
            "trend_label": "HH_HL",
            "current_close": current_close,
            "atr_used": 0.5,
            "pivot_strength_used": 3,
        },
        "provenance": {"source": "test", "warnings": []},
    }


def _make_ctx(
    tool_calls: list[tuple[str, dict[str, Any] | None]],
) -> RunContext[AgentDeps]:
    messages: list[ModelRequest | ModelResponse] = []
    if tool_calls:
        messages.append(
            ModelResponse(
                parts=[
                    ToolCallPart(tool_name=name, args={}, tool_call_id=f"tc-{i}")
                    for i, (name, _) in enumerate(tool_calls)
                ]
            )
        )
        return_parts: list[Any] = []
        for i, (name, payload) in enumerate(tool_calls):
            if payload is None:
                continue
            return_parts.append(
                ToolReturnPart(tool_name=name, content=payload, tool_call_id=f"tc-{i}")
            )
        if return_parts:
            messages.append(ModelRequest(parts=return_parts))
    messages.append(ModelRequest(parts=[UserPromptPart(content="x")]))

    deps = AgentDeps(
        session_factory=_noop_session,  # type: ignore[arg-type]
        log=structlog.get_logger("test"),
        user_id="u",
    )
    return RunContext[AgentDeps](
        deps=deps,
        model=TestModel(),
        usage=None,  # type: ignore[arg-type]
        prompt="x",
        messages=messages,
        run_step=0,
    )


def _capture_validator() -> Callable[..., Any]:
    from app.agent.validators import register_validators as _r

    captured: dict[str, Callable[..., Any]] = {}

    class _CaptureAgent:
        def output_validator(self, fn: Callable[..., Any]) -> Callable[..., Any]:
            captured["fn"] = fn
            return fn

    _r(_CaptureAgent())  # type: ignore[arg-type]
    return captured["fn"]


def _baseline_idea(*, confidence: str = "medium") -> TradeIdea:
    return TradeIdea(
        symbol="BTCUSDT",
        timeframe="1h",
        direction="long",
        regime=MarketRegime(label="trending_up", citations=[]),
        confluences=[
            Confluence(
                timeframe="1h",
                bias="bull",
                narrative="EMA21>55>200, alineación bull intacta.",
                citations=[
                    ToolCitation(
                        tool_name="get_multi_tf_confluence",
                        snapshot={"aggregate_bias": "bull"},
                    )
                ],
            )
        ],
        entry=100.0,
        entry_rationale="pullback",
        entry_citations=[
            ToolCitation(
                tool_name="get_market_structure",
                snapshot={"current_close": 100.0},
            )
        ],
        stop_loss=99.0,
        stop_loss_rationale="below swing low",
        stop_loss_citations=[
            ToolCitation(tool_name="get_market_structure", snapshot={"price": 99.0})
        ],
        targets=[
            TradeIdeaTarget(
                label="TP1",
                price=102.0,
                rationale="resistance cluster",
                citations=[
                    ToolCitation(
                        tool_name="get_market_structure",
                        snapshot={"price": 102.0},
                    )
                ],
            )
        ],
        risk_notes="slippage",
        confidence=confidence,  # type: ignore[arg-type]
        summary_es=(
            "Long en pullback al EMA21, SL bajo el swing low reciente, "
            "TP en el cluster de resistencia superior. Estructura HH-HL "
            "intacta con ADX 32 expandiendo."
        ),
        position_size_pct=100.0,
        leverage_x=1.0,
        semantic_tags=[],
    )


@pytest.mark.asyncio
async def test_validator_factor_gate_hard_veto_raises(monkeypatch) -> None:
    """When evaluate_factor_gate returns passed=False → ModelRetry."""

    async def fake_gate(*args: Any, **kwargs: Any) -> GateVerdict:
        return GateVerdict(
            passed=False,
            blocking_factors=[
                FactorBlock(
                    factor_name="ema_stack",
                    factor_tf="1h",
                    factor_kind="deterministic",
                    n_trades=150,
                    win_rate_lcb=0.22,
                    severity="hard_veto",
                )
            ],
        )

    monkeypatch.setattr("app.agent.validators.evaluate_factor_gate", fake_gate)
    validator = _capture_validator()
    ctx = _make_ctx(
        [
            ("get_multi_tf_confluence", _multi_tf_confluence_payload()),
            ("get_market_structure", _market_structure_payload()),
        ]
    )
    idea = _baseline_idea()
    with pytest.raises(ModelRetry) as excinfo:
        await validator(ctx, idea)
    msg = str(excinfo.value)
    assert "Factor gate" in msg or "factor" in msg.lower()
    assert "ema_stack" in msg


@pytest.mark.asyncio
async def test_validator_factor_gate_soft_veto_degrades_confidence(monkeypatch) -> None:
    """Soft veto forces confidence='low' + appends warning to risk_notes."""

    async def fake_gate(*args: Any, **kwargs: Any) -> GateVerdict:
        return GateVerdict(
            passed=True,
            soft_veto_factors=[
                FactorBlock(
                    factor_name="ema_stack",
                    factor_tf="1h",
                    factor_kind="deterministic",
                    n_trades=50,
                    win_rate_lcb=0.25,
                    severity="soft_veto",
                )
            ],
        )

    monkeypatch.setattr("app.agent.validators.evaluate_factor_gate", fake_gate)
    validator = _capture_validator()
    ctx = _make_ctx(
        [
            ("get_multi_tf_confluence", _multi_tf_confluence_payload()),
            ("get_market_structure", _market_structure_payload()),
        ]
    )
    idea = _baseline_idea(confidence="high")
    out = await validator(ctx, idea)
    assert out.confidence == "low"
    assert "soft veto" in out.risk_notes.lower() or "ema_stack" in out.risk_notes


@pytest.mark.asyncio
async def test_validator_factor_gate_pass_keeps_idea_intact(monkeypatch) -> None:
    """Empty verdict (no blockers, no soft vetos) leaves the idea unchanged."""

    async def fake_gate(*args: Any, **kwargs: Any) -> GateVerdict:
        return GateVerdict(passed=True)

    monkeypatch.setattr("app.agent.validators.evaluate_factor_gate", fake_gate)
    validator = _capture_validator()
    ctx = _make_ctx(
        [
            ("get_multi_tf_confluence", _multi_tf_confluence_payload()),
            ("get_market_structure", _market_structure_payload()),
        ]
    )
    idea = _baseline_idea(confidence="medium")
    out = await validator(ctx, idea)
    assert out.confidence == "medium"


@pytest.mark.asyncio
async def test_validator_factor_gate_skipped_without_confluence(monkeypatch) -> None:
    """If get_multi_tf_confluence wasn't called, no snapshot can be built and
    the factor gate is silently skipped — the trade is NOT blocked on that
    account (other gates earlier would have caught the missing tool)."""
    called = {"n": 0}

    async def fake_gate(*args: Any, **kwargs: Any) -> GateVerdict:
        called["n"] += 1
        return GateVerdict(passed=False)  # would fail if invoked

    monkeypatch.setattr("app.agent.validators.evaluate_factor_gate", fake_gate)
    validator = _capture_validator()
    # get_multi_tf_confluence absent → citation gate raises ModelRetry
    # (the baseline idea cites that tool but it wasn't called). The factor
    # gate must not have been reached.
    ctx = _make_ctx([("get_market_structure", _market_structure_payload())])
    idea = _baseline_idea()
    with pytest.raises(ModelRetry):
        await validator(ctx, idea)
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_validator_factor_gate_db_failure_logged_not_raised(
    monkeypatch,
) -> None:
    """If evaluate_factor_gate raises (DB transient failure), the validator
    logs a warning and continues — the trade is NOT blocked on infra."""

    async def fake_gate(*args: Any, **kwargs: Any) -> GateVerdict:
        raise RuntimeError("db unavailable")

    monkeypatch.setattr("app.agent.validators.evaluate_factor_gate", fake_gate)
    validator = _capture_validator()
    ctx = _make_ctx(
        [
            ("get_multi_tf_confluence", _multi_tf_confluence_payload()),
            ("get_market_structure", _market_structure_payload()),
        ]
    )
    idea = _baseline_idea(confidence="medium")
    # Should NOT raise — DB failure is swallowed at the gate level.
    out = await validator(ctx, idea)
    assert out.confidence == "medium"
