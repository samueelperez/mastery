"""A.1 — Citation contract: numeric + semantic verification.

Pinned tests for the snapshot-numerics gate and the semantic-tags gate
introduced in validators.py. These complement the existing tool-name and
handle-existence checks: they catch the "called the right tool but quoted
an invented number" and "claimed an LVN where none exists" failure modes.

The test pattern mirrors test_review_validators: we capture the validator
fn the registrar passes to ``@agent.output_validator``, then call it
directly against a synthetic ``RunContext`` carrying the messages the
gate cares about (ToolCallPart on the response side + ToolReturnPart on
the request side).
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
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
    MarketRegime,
    ToolCitation,
    TradeIdea,
    TradeIdeaTarget,
)


@dataclass
class _StubDeps:
    log: Any
    user_id: str
    session_factory: Any
    exchange: str = "binanceusdm"


@asynccontextmanager
async def _noop_session():
    yield None


def _make_ctx(
    tool_calls: list[tuple[str, dict[str, Any] | None]],
) -> RunContext[AgentDeps]:
    """Build a RunContext with both ToolCallPart (on the assistant turn)
    and ToolReturnPart (on the user turn) so the validator sees:
    - the set of tools called (via ToolCallPart)
    - the actual outputs (via ToolReturnPart, used for numeric/semantic checks)
    """
    messages: list[ModelRequest | ModelResponse] = []
    if tool_calls:
        call_parts = [
            ToolCallPart(tool_name=name, args={}, tool_call_id=f"tc-{i}")
            for i, (name, _) in enumerate(tool_calls)
        ]
        messages.append(ModelResponse(parts=call_parts))

        return_parts: list[Any] = []
        for i, (name, payload) in enumerate(tool_calls):
            if payload is None:
                continue
            return_parts.append(
                ToolReturnPart(
                    tool_name=name,
                    content=payload,
                    tool_call_id=f"tc-{i}",
                )
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
    """Capture the validator fn from register_validators without needing
    a real Agent instance."""
    from app.agent.validators import register_validators as _r

    captured: dict[str, Callable[..., Any]] = {}

    class _CaptureAgent:
        def output_validator(self, fn: Callable[..., Any]) -> Callable[..., Any]:
            captured["fn"] = fn
            return fn

    _r(_CaptureAgent())  # type: ignore[arg-type]
    return captured["fn"]


def _baseline_idea(
    *,
    entry: float = 100.0,
    stop_loss: float = 99.0,
    target_price: float = 102.0,
    entry_citations: list[ToolCitation] | None = None,
    sl_citations: list[ToolCitation] | None = None,
    target_citations: list[ToolCitation] | None = None,
    confluence_citations: list[ToolCitation] | None = None,
    semantic_tags: list[str] | None = None,
    confidence: str = "medium",
) -> TradeIdea:
    """A direction='long' TradeIdea satisfying every other validator check
    (R:R, side↔bias, sizing) so we can isolate the gate under test.
    R:R = (102-100)/(100-99) = 2.0 — above 1.5 threshold."""
    return TradeIdea(
        symbol="BTCUSDT",
        timeframe="1h",
        direction="long",
        regime=MarketRegime(label="trending_up", citations=[]),
        confluences=[
            Confluence(
                timeframe="1h",
                bias="bull",
                narrative=(
                    "EMA21>55>200 con close 0.5 ATR sobre la media — alineación bull intacta."
                ),
                citations=confluence_citations
                if confluence_citations is not None
                else [
                    ToolCitation(
                        tool_name="get_multi_tf_confluence",
                        snapshot={"aggregate_bias": "bull"},
                    )
                ],
            )
        ],
        entry=entry,
        entry_rationale="pullback to EMA21",
        entry_citations=entry_citations
        if entry_citations is not None
        else [
            ToolCitation(
                tool_name="get_market_structure",
                snapshot={"current_close": entry},
            )
        ],
        stop_loss=stop_loss,
        stop_loss_rationale="below recent swing low",
        stop_loss_citations=sl_citations
        if sl_citations is not None
        else [
            ToolCitation(
                tool_name="get_market_structure",
                snapshot={"price": stop_loss},
            )
        ],
        targets=[
            TradeIdeaTarget(
                label="TP1",
                price=target_price,
                rationale="resistance cluster",
                citations=target_citations
                if target_citations is not None
                else [
                    ToolCitation(
                        tool_name="get_market_structure",
                        snapshot={"price": target_price},
                    )
                ],
            )
        ],
        risk_notes="slippage in low-liquidity hours",
        confidence=confidence,  # type: ignore[arg-type]
        summary_es=(
            "Long en pullback al EMA21, SL bajo el swing low reciente, "
            "TP en el cluster de resistencia superior. Estructura HH-HL "
            "intacta con ADX 32 expandiendo."
        ),
        position_size_pct=100.0,
        leverage_x=1.0,
        semantic_tags=semantic_tags or [],
    )


def _multi_tf_confluence_payload(*, aggregate_bias: str = "bull") -> dict[str, Any]:
    """Minimal shape get_multi_tf_confluence returns. Satisfies the
    side↔bias gate (direction='long' needs bias != 'bear')."""
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


def _market_structure_payload(
    *,
    current_close: float = 100.0,
    support_prices: list[float] | None = None,
    resistance_prices: list[float] | None = None,
) -> dict[str, Any]:
    return {
        "data": {
            "swing_highs": [],
            "swing_lows": [],
            "support": [
                {"price": p, "touches": 2, "last_touch_ts": "2026-01-01T00:00:00Z"}
                for p in (support_prices or [])
            ],
            "resistance": [
                {"price": p, "touches": 2, "last_touch_ts": "2026-01-01T00:00:00Z"}
                for p in (resistance_prices or [])
            ],
            "trend_label": "HH_HL",
            "current_close": current_close,
            "atr_used": 0.5,
            "pivot_strength_used": 3,
        },
        "provenance": {"source": "test", "warnings": []},
    }


def _volume_profile_payload(*, has_lvn: bool, poc_price: float = 100.0) -> dict[str, Any]:
    """Minimal volume profile output, optionally with LVN nodes far from
    the trade levels so they don't trip the LVN-targets gate."""
    lvns = (
        [
            {"price": 95.0, "volume": 5.0, "pct_of_poc": 25.0},
        ]
        if has_lvn
        else []
    )
    return {
        "data": {
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "lookback_bars": 200,
            "bins": 50,
            "poc_price": poc_price,
            "poc_volume": 20.0,
            "high_volume_nodes": [],
            "low_volume_nodes": lvns,
            "range_low": 90.0,
            "range_high": 110.0,
            "interpretation": "test",
        },
        "provenance": {"source": "test", "warnings": []},
    }


# ----------------------------------------------------------------------------
# Numeric snapshot verification
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_numeric_match_passes() -> None:
    """Citation snapshot value matches tool output → validator accepts."""
    validator = _capture_validator()
    ctx = _make_ctx(
        [
            ("get_multi_tf_confluence", _multi_tf_confluence_payload()),
            (
                "get_market_structure",
                _market_structure_payload(current_close=100.0),
            ),
        ]
    )
    idea = _baseline_idea(
        entry=100.0,
        entry_citations=[
            ToolCitation(
                tool_name="get_market_structure",
                snapshot={"current_close": 100.0},
            )
        ],
    )
    out = await validator(ctx, idea)
    assert out is idea


@pytest.mark.asyncio
async def test_snapshot_numeric_mismatch_raises() -> None:
    """Citation cites a tool that was called but the snapshot value diverges
    from the tool's actual output → ModelRetry."""
    validator = _capture_validator()
    ctx = _make_ctx(
        [
            ("get_multi_tf_confluence", _multi_tf_confluence_payload()),
            (
                "get_market_structure",
                _market_structure_payload(current_close=100.0),
            ),
        ]
    )
    # Claim entry=100.0 but cite current_close=120.0 (real was 100.0).
    idea = _baseline_idea(
        entry=100.0,
        entry_citations=[
            ToolCitation(
                tool_name="get_market_structure",
                snapshot={"current_close": 120.0},
            )
        ],
    )
    with pytest.raises(ModelRetry) as excinfo:
        await validator(ctx, idea)
    msg = str(excinfo.value).lower()
    assert "snapshot" in msg or "mismatch" in msg
    assert "current_close" in str(excinfo.value)


@pytest.mark.asyncio
async def test_snapshot_numeric_within_tolerance_passes() -> None:
    """0.05% diff is within the 0.1% tolerance → passes."""
    validator = _capture_validator()
    ctx = _make_ctx(
        [
            ("get_multi_tf_confluence", _multi_tf_confluence_payload()),
            (
                "get_market_structure",
                _market_structure_payload(current_close=100.000),
            ),
        ]
    )
    idea = _baseline_idea(
        entry_citations=[
            ToolCitation(
                tool_name="get_market_structure",
                snapshot={"current_close": 100.05},  # 0.05% diff
            )
        ],
    )
    out = await validator(ctx, idea)
    assert out is idea


@pytest.mark.asyncio
async def test_snapshot_numeric_unverifiable_key_passes() -> None:
    """Snapshot key not present in tool output → unverifiable, soft-pass.
    (Agent may have derived the value from outputs, e.g. midpoint.)"""
    validator = _capture_validator()
    ctx = _make_ctx(
        [
            ("get_multi_tf_confluence", _multi_tf_confluence_payload()),
            (
                "get_market_structure",
                _market_structure_payload(current_close=100.0),
            ),
        ]
    )
    idea = _baseline_idea(
        entry_citations=[
            ToolCitation(
                tool_name="get_market_structure",
                snapshot={"midpoint_estimate": 99.5},  # derived, not in output
            )
        ],
    )
    out = await validator(ctx, idea)
    assert out is idea


@pytest.mark.asyncio
async def test_snapshot_handle_keys_skipped() -> None:
    """Handle keys like ``run_id`` are validated by the handle gate, not the
    numeric one — a string handle in snapshot doesn't crash the numeric path."""
    validator = _capture_validator()
    # Returned handles include this run_id; the agent cites it correctly.
    ms_payload = _market_structure_payload(current_close=100.0)
    confluence_payload = _multi_tf_confluence_payload()
    ctx = _make_ctx(
        [
            ("get_multi_tf_confluence", confluence_payload),
            ("get_market_structure", ms_payload),
        ]
    )
    idea = _baseline_idea(
        entry_citations=[
            ToolCitation(
                tool_name="get_market_structure",
                snapshot={"current_close": 100.0, "timeframe": "1h"},
            )
        ],
    )
    out = await validator(ctx, idea)
    assert out is idea


# ----------------------------------------------------------------------------
# Semantic tag verification
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_semantic_tag_lvn_supported_passes() -> None:
    """``lvn_support`` tag with LVN nodes present in get_volume_profile →
    confidence stays untouched."""
    validator = _capture_validator()
    ctx = _make_ctx(
        [
            ("get_multi_tf_confluence", _multi_tf_confluence_payload()),
            (
                "get_market_structure",
                _market_structure_payload(current_close=100.0),
            ),
            ("get_volume_profile", _volume_profile_payload(has_lvn=True)),
        ]
    )
    idea = _baseline_idea(semantic_tags=["lvn_support"], confidence="high")
    out = await validator(ctx, idea)
    # confidence preserved because LVN structure was present
    assert out.confidence == "high"


@pytest.mark.asyncio
async def test_semantic_tag_lvn_unsupported_degrades_confidence() -> None:
    """``lvn_support`` tag but no LVN nodes returned → degrade confidence
    from 'high' to 'medium' and warn in risk_notes."""
    validator = _capture_validator()
    ctx = _make_ctx(
        [
            ("get_multi_tf_confluence", _multi_tf_confluence_payload()),
            (
                "get_market_structure",
                _market_structure_payload(current_close=100.0),
            ),
            ("get_volume_profile", _volume_profile_payload(has_lvn=False)),
        ]
    )
    idea = _baseline_idea(semantic_tags=["lvn_support"], confidence="high")
    out = await validator(ctx, idea)
    assert out.confidence == "medium"
    assert "lvn_support" in out.risk_notes.lower() or "lvn" in out.risk_notes.lower()


@pytest.mark.asyncio
async def test_semantic_tag_unregistered_passes_through() -> None:
    """Tags without a registered structure check (interpretive tags like
    ``post_news_breakout``, ``mean_reversion_setup``) are accepted without
    structural verification — they're noted but not gated."""
    validator = _capture_validator()
    ctx = _make_ctx(
        [
            ("get_multi_tf_confluence", _multi_tf_confluence_payload()),
            (
                "get_market_structure",
                _market_structure_payload(current_close=100.0),
            ),
        ]
    )
    idea = _baseline_idea(semantic_tags=["post_news_breakout"], confidence="high")
    out = await validator(ctx, idea)
    # No degradation since the tag has no registered check
    assert out.confidence == "high"


@pytest.mark.asyncio
async def test_semantic_tag_lvn_without_volume_profile_tool_degrades() -> None:
    """``lvn_support`` claimed but ``get_volume_profile`` was never called →
    confidence degraded."""
    validator = _capture_validator()
    ctx = _make_ctx(
        [
            ("get_multi_tf_confluence", _multi_tf_confluence_payload()),
            (
                "get_market_structure",
                _market_structure_payload(current_close=100.0),
            ),
            # get_volume_profile deliberately absent
        ]
    )
    idea = _baseline_idea(semantic_tags=["lvn_resistance"], confidence="high")
    out = await validator(ctx, idea)
    assert out.confidence == "medium"
