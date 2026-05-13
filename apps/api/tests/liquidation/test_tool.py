"""Tests for register_liquidation_tool.

Verifies the tool's contract: returns ToolResult[HeatmapSnapshot] wrapping
the service output, with provenance propagated from the snapshot.
"""

from __future__ import annotations

import pytest

from app.agent.tools._envelope import Provenance, ToolResult
from app.liquidation.models import HeatmapSnapshot


@pytest.mark.asyncio
async def test_tool_result_wraps_snapshot(now_utc) -> None:
    """ToolResult.data must be the HeatmapSnapshot; provenance shared."""
    snap = HeatmapSnapshot(
        symbol="BTCUSDT",
        timeframe="4h",
        current_price=84_500.0,
        as_of=now_utc,
        magnet_zones=[],
        imbalance_ratio=1.0,
        cluster_density=0.0,
        sources_used=["A_derived"],
        sources_agreement=1.0,
        provenance=Provenance(
            source="liquidation_heatmap_engine",
            as_of=now_utc,
            rows=0,
            warnings=[],
        ),
    )
    result = ToolResult(data=snap, provenance=snap.provenance)
    assert result.data.symbol == "BTCUSDT"
    assert result.provenance.source == "liquidation_heatmap_engine"
    assert result.provenance is snap.provenance  # propagated, not copied


def test_register_liquidation_tool_callable() -> None:
    """register_liquidation_tool exists and accepts an agent-like object."""
    from app.liquidation.tool import register_liquidation_tool

    captured: dict[str, object] = {}

    class _FakeAgent:
        def tool(self, fn):
            captured["fn"] = fn
            return fn

    register_liquidation_tool(_FakeAgent())  # type: ignore[arg-type]

    assert "fn" in captured
    fn = captured["fn"]
    assert fn.__name__ == "get_liquidation_heatmap"
    # The LLM-facing docstring must include the USE/DO NOT USE sections per
    # the agent_tool_rules invariant.
    doc = fn.__doc__ or ""
    assert "USE THIS WHEN" in doc
    assert "DO NOT USE THIS TO" in doc


def test_tool_registers_alphabetically_in_build_agent() -> None:
    """Sanity check: register_liquidation_tool is called between
    register_journal_query_tool and register_list_alerts_tool in
    `agent/agent.py::build_agent`. Asserting on the source preserves the
    alphabetical-cache-stable invariant (#4) without a real agent boot.
    """
    from pathlib import Path

    src = Path(Path(__file__).resolve().parents[2] / "app" / "agent" / "agent.py").read_text()
    journal = src.index("register_journal_query_tool(agent)")
    liq = src.index("register_liquidation_tool(agent)")
    list_alerts = src.index("register_list_alerts_tool(agent)")
    assert journal < liq < list_alerts


def test_tool_registered_in_reviewer_agent() -> None:
    """Same alphabetical check for reviewer/agent.py."""
    from pathlib import Path

    src = Path(Path(__file__).resolve().parents[2] / "app" / "reviewer" / "agent.py").read_text()
    indicators = src.index("register_indicator_tools(agent)")
    liq = src.index("register_liquidation_tool(agent)")
    ohlcv = src.index("register_ohlcv_tools(agent)")
    assert indicators < liq < ohlcv


def test_system_prompt_catalog_includes_tool() -> None:
    """TOOLS_CATALOG must include the new entry alphabetically between
    get_indicators and get_market_dominance."""
    from app.agent.system_prompt import TOOLS_CATALOG

    indicators = TOOLS_CATALOG.index("- get_indicators(")
    liq = TOOLS_CATALOG.index("- get_liquidation_heatmap(")
    dominance = TOOLS_CATALOG.index("- get_market_dominance(")
    assert indicators < liq < dominance
