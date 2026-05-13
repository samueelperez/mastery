"""Unit tests for the auto-approve helpers in ``scout_dispatcher`` (PR-08).

The DB-touching part (``_maybe_auto_approve``) is exercised in integration
tests through ``dispatch_scout_match``. These pinpoint tests cover the
pure helper that pulls ``sources_agreement`` out of a heatmap citation —
the single decision input the gate reads.
"""

from __future__ import annotations

import os

# Stub the API key so ``scout_dispatcher`` import chain (which transitively
# touches ``agent.agent``) doesn't blow up at construction time.
os.environ.setdefault("OPENROUTER_API_KEY", "sk-test-placeholder")

from types import SimpleNamespace

import pytest

from app.setups.scout_dispatcher import (
    _heatmap_agreement,
    settings_snapshot_agreement,
)


def _citation(tool_name: str, snapshot: dict) -> SimpleNamespace:
    return SimpleNamespace(tool_name=tool_name, snapshot=snapshot)


def _idea_with(citation: SimpleNamespace | None) -> SimpleNamespace:
    """TradeIdea-shaped duck type with citation slots ``find_heatmap_citation_snapshot``
    inspects."""
    citations = [citation] if citation else []
    return SimpleNamespace(
        entry_citations=[],
        stop_loss_citations=[],
        targets=[SimpleNamespace(citations=citations)],
        invalidation_conditions=[],
    )


def test_heatmap_agreement_returns_float_when_cited():
    idea = _idea_with(
        _citation(
            "get_liquidation_heatmap",
            {
                "symbol": "BTCUSDT",
                "current_price": 80_000.0,
                "sources_agreement": 0.92,
                "sources_used": ["A_derived", "B_hyperliquid"],
                "nearest_short_liq_price": 84_000.0,
            },
        )
    )
    assert _heatmap_agreement(idea) == pytest.approx(0.92)


def test_heatmap_agreement_returns_none_without_citation():
    idea = _idea_with(_citation("get_market_structure", {"price": 1.0}))
    assert _heatmap_agreement(idea) is None


def test_heatmap_agreement_returns_none_when_field_missing():
    idea = _idea_with(
        _citation(
            "get_liquidation_heatmap",
            {
                "symbol": "BTCUSDT",
                "current_price": 80_000.0,
                "sources_used": ["A_derived"],
            },
        )
    )
    assert _heatmap_agreement(idea) is None


def test_settings_snapshot_agreement_formats_for_telegram():
    idea = _idea_with(
        _citation(
            "get_liquidation_heatmap",
            {
                "symbol": "BTCUSDT",
                "current_price": 80_000.0,
                "sources_agreement": 0.918,
                "sources_used": ["A_derived"],
            },
        )
    )
    assert settings_snapshot_agreement(idea) == "0.92"


def test_settings_snapshot_agreement_renders_dash_when_no_citation():
    idea = _idea_with(None)
    assert settings_snapshot_agreement(idea) == "—"
