"""Tests para post_mortem_validators — gates específicas del PostMortem.

Test directo de los helpers `_is_valid_factor_key` y el behavior del
validator registrado contra un PostMortem instanciado a mano. Mock minimal
de RunContext.messages para simular tool calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
import structlog
from pydantic_ai import ModelRetry
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
)

from app.agent.deps import AgentDeps
from app.agent.models import PostMortem, ToolCitation
from app.agent.post_mortem_validators import (
    _is_valid_factor_key,
    register_post_mortem_validators,
)


class TestIsValidFactorKey:
    @pytest.mark.parametrize(
        "key,expected",
        [
            ("ema_stack@1h", True),
            ("rsi@4h", True),
            ("volume@15m", True),
            ("distance_atr@1d", True),
            ("lvn_support", True),
            ("fvg_fill", True),
            ("ema_stack@30m", False),  # 30m no es TF válido
            ("ema_stack@", False),
            ("@1h", False),
            ("", False),
            ("EMA_STACK", False),  # uppercase no permitido en semantic
            ("ema stack", False),  # espacio
            ("ema-stack", False),  # guion
        ],
    )
    def test_valid_keys(self, key: str, expected: bool) -> None:
        assert _is_valid_factor_key(key) is expected


# --- Validator end-to-end --------------------------------------------------


@dataclass
class _FakeAgent:
    """Mini-spy de pydantic_ai.Agent — capturamos el validator registrado
    para poder invocarlo directo en los tests."""

    validators: list[Any]

    def output_validator(self, fn: Any) -> Any:  # noqa: D401
        self.validators.append(fn)
        return fn


class _FakeCtx:
    def __init__(self, *, tool_calls: list[str]) -> None:
        self.deps = AgentDeps(
            session_factory=None,  # type: ignore[arg-type]
            log=structlog.get_logger("test"),
            user_id="test-user",
        )
        # ModelResponse with ToolCallParts mimicking the tools the agent
        # called this turn.
        parts = [
            ToolCallPart(tool_name=name, args={}, tool_call_id=f"tc_{i}")
            for i, name in enumerate(tool_calls)
        ]
        self.messages = [
            ModelRequest(parts=[]),  # user message stub
            ModelResponse(parts=parts),
        ]


def _make_pm(**overrides: Any) -> PostMortem:
    """Helper para construir un PostMortem válido por default."""
    defaults: dict[str, Any] = {
        "setup_id": "trade-123",
        "verdict": "thesis_held",
        "failure_factors": [],
        "success_factors": ["ema_stack@1h"],
        "lesson_es": (
            "En régimen trending_up, EMA stack alineado por 4h sostiene la "
            "ganancia — exigir esta confluencia antes de proponer long."
        ),
        "confidence_calibration": "calibrated",
        "counterfactual_es": None,
        "citations": [
            ToolCitation(tool_name="get_indicators", snapshot={"ema_21": 78000})
        ],
    }
    defaults.update(overrides)
    return PostMortem(**defaults)


def _get_validator() -> Any:
    fake_agent = _FakeAgent(validators=[])
    register_post_mortem_validators(fake_agent)  # type: ignore[arg-type]
    assert len(fake_agent.validators) == 1
    return fake_agent.validators[0]


@pytest.mark.asyncio
async def test_citations_must_match_called_tools() -> None:
    validator = _get_validator()
    pm = _make_pm()
    # Agente no llamó get_indicators este turn → rebota.
    ctx = _FakeCtx(tool_calls=["get_ohlcv"])
    with pytest.raises(ModelRetry):
        await validator(ctx, pm)


@pytest.mark.asyncio
async def test_banned_tool_citation_rejected() -> None:
    validator = _get_validator()
    pm = _make_pm(
        citations=[
            ToolCitation(
                tool_name="get_multi_tf_confluence",
                snapshot={"aggregate_bias": "bull"},
            ),
        ],
    )
    ctx = _FakeCtx(tool_calls=["get_multi_tf_confluence"])
    with pytest.raises(ModelRetry):
        await validator(ctx, pm)


@pytest.mark.asyncio
async def test_thesis_held_requires_success_factor() -> None:
    validator = _get_validator()
    pm = _make_pm(verdict="thesis_held", success_factors=[], failure_factors=[])
    ctx = _FakeCtx(tool_calls=["get_indicators"])
    with pytest.raises(ModelRetry) as exc:
        await validator(ctx, pm)
    assert "success_factors" in str(exc.value)


@pytest.mark.asyncio
async def test_thesis_broken_requires_failure_factor() -> None:
    validator = _get_validator()
    pm = _make_pm(
        verdict="thesis_broken", failure_factors=[], success_factors=["ema_stack@1h"]
    )
    ctx = _FakeCtx(tool_calls=["get_indicators"])
    with pytest.raises(ModelRetry) as exc:
        await validator(ctx, pm)
    assert "failure_factors" in str(exc.value)


@pytest.mark.asyncio
async def test_noise_rejects_attribution() -> None:
    validator = _get_validator()
    pm = _make_pm(
        verdict="noise",
        success_factors=["ema_stack@1h"],
        failure_factors=[],
    )
    ctx = _FakeCtx(tool_calls=["get_indicators"])
    with pytest.raises(ModelRetry):
        await validator(ctx, pm)


@pytest.mark.asyncio
async def test_invalid_factor_key_shape_rejected() -> None:
    validator = _get_validator()
    pm = _make_pm(
        success_factors=["EMA_STACK@30m"],  # uppercase + invalid TF
        failure_factors=[],
    )
    ctx = _FakeCtx(tool_calls=["get_indicators"])
    with pytest.raises(ModelRetry):
        await validator(ctx, pm)


@pytest.mark.asyncio
async def test_happy_path_passes() -> None:
    validator = _get_validator()
    pm = _make_pm()
    ctx = _FakeCtx(tool_calls=["get_indicators", "get_market_structure"])
    out = await validator(ctx, pm)
    assert out is pm
