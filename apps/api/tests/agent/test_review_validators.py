"""Tests del validator de `review_agent` — coherencia state/recommendation +
citation tool_name discriminator.

Construimos un Agent[AgentDeps, TradeReview] mínimo con `TestModel` para que
el validator vea `ctx.messages` (tool calls) y pueda rebotar como en producción.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import pytest
import structlog
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel

from app.agent.deps import AgentDeps
from app.agent.models import ToolCitation, TradeReview


@dataclass
class _StubDeps:
    log: Any
    user_id: str


@asynccontextmanager
async def _noop_session():
    yield None


def _ctx_with_tools(tool_names: list[str]) -> RunContext[AgentDeps]:
    """Build a RunContext con tool_calls anteriores. El validator solo lee
    `ctx.messages` para discriminar por tool_name."""
    messages: list[ModelRequest | ModelResponse] = []
    if tool_names:
        # ModelResponse con ToolCallPart por cada tool "llamada".
        parts = [
            ToolCallPart(tool_name=t, args={}, tool_call_id=f"tc-{i}")
            for i, t in enumerate(tool_names)
        ]
        messages.append(ModelResponse(parts=parts))
    # User prompt vacío — no influye en el validator.
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


def _review(
    *,
    state: str = "on_track",
    recommendation: str = "hold",
    citations: list[ToolCitation] | None = None,
) -> TradeReview:
    return TradeReview(
        summary="ok",
        current_state=state,  # type: ignore[arg-type]
        recommendation=recommendation,  # type: ignore[arg-type]
        rationale="ok",
        citations=citations
        if citations is not None
        else [ToolCitation(tool_name="get_indicators", snapshot={"rsi": 62})],
    )


def _get_validator_callable(
    agent: Agent[AgentDeps, TradeReview],
) -> Callable[..., Any]:
    """register_review_validators decora un fn con @agent.output_validator,
    pero el fn local no es accesible. Tomamos el último validator registrado
    desde el agent state interno."""
    # Pydantic-AI stores them on the agent; the public API varies by version,
    # so we re-import the implementation and call it directly.
    from app.agent.review_validators import register_review_validators as _r

    captured: dict[str, Callable[..., Any]] = {}

    class _CaptureAgent:
        def output_validator(self, fn: Callable[..., Any]) -> Callable[..., Any]:
            captured["fn"] = fn
            return fn

    _r(_CaptureAgent())  # type: ignore[arg-type]
    return captured["fn"]


@pytest.mark.asyncio
async def test_reversing_with_hold_raises() -> None:
    validator = _get_validator_callable(Agent[AgentDeps, TradeReview])  # type: ignore[type-abstract]
    ctx = _ctx_with_tools(["get_indicators"])
    review = _review(state="reversing", recommendation="hold")
    with pytest.raises(Exception) as excinfo:
        await validator(ctx, review)
    assert "reversing" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_on_track_with_exit_now_raises() -> None:
    validator = _get_validator_callable(Agent[AgentDeps, TradeReview])  # type: ignore[type-abstract]
    ctx = _ctx_with_tools(["get_indicators"])
    review = _review(state="on_track", recommendation="exit_now")
    with pytest.raises(Exception) as excinfo:
        await validator(ctx, review)
    assert "on_track" in str(excinfo.value).lower() or "exit_now" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_citation_tool_not_called_raises() -> None:
    validator = _get_validator_callable(Agent[AgentDeps, TradeReview])  # type: ignore[type-abstract]
    # Tools llamadas: SOLO indicators. La review cita confluence → debe rebotar.
    ctx = _ctx_with_tools(["get_indicators"])
    review = _review(
        citations=[ToolCitation(tool_name="get_multi_tf_confluence", snapshot={})],
    )
    with pytest.raises(Exception) as excinfo:
        await validator(ctx, review)
    assert "get_multi_tf_confluence" in str(excinfo.value)


@pytest.mark.asyncio
async def test_coherent_review_passes() -> None:
    validator = _get_validator_callable(Agent[AgentDeps, TradeReview])  # type: ignore[type-abstract]
    ctx = _ctx_with_tools(["get_indicators"])
    review = _review(state="at_risk", recommendation="tighten_sl")
    # No raise → return value es el mismo review.
    out = await validator(ctx, review)
    assert out is review
