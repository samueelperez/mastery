"""Validación del modelo `TradeReview` (lightweight: solo Pydantic, sin agent).

Cubrimos:
- length caps (summary ≤400, rationale ≤600).
- Citations ≥1.
- Enums de current_state y recommendation.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agent.models import ToolCitation, TradeReview


def _cit() -> ToolCitation:
    return ToolCitation(
        tool_name="get_indicators",
        snapshot={"rsi": 62.0},
    )


def test_trade_review_minimal_ok() -> None:
    r = TradeReview(
        summary="Estructura HH-HL intacta. RSI 62 sin agotamiento.",
        current_state="on_track",
        recommendation="hold",
        rationale="EMA21 4h soporta. ADX 28 expandiendo.",
        citations=[_cit()],
    )
    assert r.recommendation == "hold"
    assert r.current_state == "on_track"


def test_trade_review_summary_too_long() -> None:
    with pytest.raises(ValidationError):
        TradeReview(
            summary="x" * 401,
            current_state="on_track",
            recommendation="hold",
            rationale="ok",
            citations=[_cit()],
        )


def test_trade_review_rationale_too_long() -> None:
    with pytest.raises(ValidationError):
        TradeReview(
            summary="ok",
            current_state="on_track",
            recommendation="hold",
            rationale="x" * 601,
            citations=[_cit()],
        )


def test_trade_review_requires_at_least_one_citation() -> None:
    with pytest.raises(ValidationError):
        TradeReview(
            summary="ok",
            current_state="on_track",
            recommendation="hold",
            rationale="ok",
            citations=[],
        )


def test_trade_review_state_enum_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        TradeReview(
            summary="ok",
            current_state="bullish",  # type: ignore[arg-type]
            recommendation="hold",
            rationale="ok",
            citations=[_cit()],
        )


def test_trade_review_recommendation_enum_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        TradeReview(
            summary="ok",
            current_state="on_track",
            recommendation="scale_in",  # type: ignore[arg-type]
            rationale="ok",
            citations=[_cit()],
        )
