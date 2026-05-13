"""Unit tests for ``app/liquidation/factor_snapshot.py``.

These pure-function helpers bridge the LLM's citation snapshot to the
enriched dict written under ``journal_trades.factor_snapshot.get_liquidation_heatmap``.
The handler that closes the M1 ground-truth loop
(``liquidation/telegram_handlers.py::record_ground_truth``) only fires
correctly when this enrichment is in place.
"""

from __future__ import annotations

from types import SimpleNamespace

from pydantic_ai.messages import ModelRequest, ToolReturnPart

from app.liquidation.factor_snapshot import (
    enrich_with_provider_breakdown,
    find_heatmap_citation_snapshot,
)


def _citation(tool_name: str, snapshot: dict) -> SimpleNamespace:
    return SimpleNamespace(tool_name=tool_name, snapshot=snapshot)


def _output(
    *,
    entry_citations: list | None = None,
    stop_loss_citations: list | None = None,
    target_citations: list | None = None,
    invalidation_citations: list | None = None,
) -> SimpleNamespace:
    """Build a TradeIdea-shaped duck type carrying only the citation slots
    the helper inspects."""
    return SimpleNamespace(
        entry_citations=entry_citations or [],
        stop_loss_citations=stop_loss_citations or [],
        targets=(
            [SimpleNamespace(citations=target_citations)] if target_citations else []
        ),
        invalidation_conditions=(
            [SimpleNamespace(citations=invalidation_citations)]
            if invalidation_citations
            else []
        ),
    )


def _heatmap_tool_return(
    *,
    symbol: str = "BTCUSDT",
    timeframe: str = "4h",
    nearest_short_liq: dict | None = None,
    nearest_long_liq: dict | None = None,
) -> list[ModelRequest]:
    data: dict = {"symbol": symbol, "timeframe": timeframe}
    if nearest_short_liq is not None:
        data["nearest_short_liq"] = nearest_short_liq
    if nearest_long_liq is not None:
        data["nearest_long_liq"] = nearest_long_liq
    return [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="get_liquidation_heatmap",
                    content={"data": data, "provenance": {}},
                    tool_call_id="tc-0",
                )
            ]
        )
    ]


# ---------------------------------------------------------------------------
# find_heatmap_citation_snapshot
# ---------------------------------------------------------------------------


def test_find_returns_none_when_no_heatmap_citation():
    out = _output(
        entry_citations=[_citation("get_market_structure", {"current_close": 80000})],
    )
    assert find_heatmap_citation_snapshot(out) is None


def test_find_returns_target_citation_snapshot():
    snap = {
        "symbol": "BTCUSDT",
        "current_price": 80_000.0,
        "sources_agreement": 0.91,
        "sources_used": ["A_derived", "B_hyperliquid"],
        "nearest_short_liq_price": 84_000.0,
    }
    out = _output(target_citations=[_citation("get_liquidation_heatmap", snap)])
    found = find_heatmap_citation_snapshot(out)
    assert found == snap
    # Must be a copy — mutating the returned dict must not affect the citation.
    assert found is not snap
    found["mutated"] = True
    assert "mutated" not in snap


def test_find_scans_invalidation_conditions():
    snap = {
        "symbol": "ETHUSDT",
        "current_price": 3_000.0,
        "sources_agreement": 0.7,
        "sources_used": ["A_derived"],
        "nearest_long_liq_price": 2_900.0,
    }
    out = _output(
        invalidation_citations=[_citation("get_liquidation_heatmap", snap)],
    )
    assert find_heatmap_citation_snapshot(out) == snap


# ---------------------------------------------------------------------------
# enrich_with_provider_breakdown
# ---------------------------------------------------------------------------


def test_enrich_no_matching_tool_return_is_noop():
    snap = {
        "symbol": "BTCUSDT",
        "current_price": 80_000.0,
        "sources_agreement": 0.91,
        "sources_used": ["A_derived", "B_hyperliquid"],
        "nearest_short_liq_price": 84_000.0,
    }
    result = enrich_with_provider_breakdown(dict(snap), messages=[])
    assert result == snap


def test_enrich_short_liq_with_both_providers_contributing():
    snap = {
        "symbol": "BTCUSDT",
        "current_price": 80_000.0,
        "sources_agreement": 0.91,
        "sources_used": ["A_derived", "B_hyperliquid"],
        "nearest_short_liq_price": 84_000.0,
    }
    messages = _heatmap_tool_return(
        nearest_short_liq={
            "price_low": 83_900.0,
            "price_high": 84_100.0,
            "side": "short_liq",
            "source_breakdown": {"A_derived": 120_000_000.0, "B_hyperliquid": 60_000_000.0},
        }
    )
    out = enrich_with_provider_breakdown(snap, messages)
    assert out["timeframe"] == "4h"
    assert out["source_breakdown_a_price"] == 84_000.0
    assert out["source_breakdown_b_price"] == 84_000.0


def test_enrich_long_liq_only_one_provider_contributes():
    snap = {
        "symbol": "BTCUSDT",
        "current_price": 80_000.0,
        "sources_agreement": 0.55,
        "sources_used": ["A_derived", "B_hyperliquid"],
        "nearest_long_liq_price": 76_000.0,
    }
    messages = _heatmap_tool_return(
        nearest_long_liq={
            "price_low": 75_900.0,
            "price_high": 76_100.0,
            "side": "long_liq",
            # Only A contributed; B's bucket is zero (e.g. no relevant
            # Hyperliquid addresses in this band).
            "source_breakdown": {"A_derived": 80_000_000.0, "B_hyperliquid": 0.0},
        }
    )
    out = enrich_with_provider_breakdown(snap, messages)
    assert out["source_breakdown_a_price"] == 76_000.0
    assert "source_breakdown_b_price" not in out


def test_enrich_skips_when_symbol_mismatch():
    snap = {
        "symbol": "BTCUSDT",
        "current_price": 80_000.0,
        "sources_agreement": 0.9,
        "sources_used": ["A_derived"],
        "nearest_short_liq_price": 84_000.0,
    }
    messages = _heatmap_tool_return(
        symbol="ETHUSDT",
        nearest_short_liq={
            "price_low": 3_100.0,
            "price_high": 3_110.0,
            "side": "short_liq",
            "source_breakdown": {"A_derived": 1.0, "B_hyperliquid": 1.0},
        },
    )
    out = enrich_with_provider_breakdown(snap, messages)
    assert "source_breakdown_a_price" not in out
    assert "source_breakdown_b_price" not in out
    assert "timeframe" not in out


def test_enrich_agreement_only_citation_gets_timeframe_but_no_prices():
    """Entry citations can legitimately reference only sources_agreement
    without naming a specific zone. The handler picks no proposed price
    in that case — and that's by design — but timeframe still helps the
    calibration job slot the row into the right (symbol, tf) cell."""
    snap = {
        "symbol": "BTCUSDT",
        "current_price": 80_000.0,
        "sources_agreement": 0.91,
        "sources_used": ["A_derived", "B_hyperliquid"],
    }
    messages = _heatmap_tool_return(
        nearest_short_liq={
            "price_low": 83_900.0,
            "price_high": 84_100.0,
            "side": "short_liq",
            "source_breakdown": {"A_derived": 1.0, "B_hyperliquid": 1.0},
        }
    )
    out = enrich_with_provider_breakdown(snap, messages)
    assert out["timeframe"] == "4h"
    assert "source_breakdown_a_price" not in out
    assert "source_breakdown_b_price" not in out


def test_enrich_uses_latest_tool_return_when_multiple_calls():
    """If the agent calls the heatmap tool twice in one turn, the citation
    is matched against the latest return (most recent state)."""
    snap = {
        "symbol": "BTCUSDT",
        "current_price": 80_000.0,
        "sources_agreement": 0.9,
        "sources_used": ["A_derived", "B_hyperliquid"],
        "nearest_short_liq_price": 84_000.0,
    }
    messages: list = []
    # First (stale) call — only B contributed.
    messages.extend(
        _heatmap_tool_return(
            nearest_short_liq={
                "price_low": 83_900.0,
                "price_high": 84_100.0,
                "side": "short_liq",
                "source_breakdown": {"A_derived": 0.0, "B_hyperliquid": 1.0},
            }
        )
    )
    # Second (latest) — A also contributes now.
    messages.extend(
        _heatmap_tool_return(
            nearest_short_liq={
                "price_low": 83_900.0,
                "price_high": 84_100.0,
                "side": "short_liq",
                "source_breakdown": {"A_derived": 1.0, "B_hyperliquid": 1.0},
            }
        )
    )
    out = enrich_with_provider_breakdown(snap, messages)
    assert out["source_breakdown_a_price"] == 84_000.0
    assert out["source_breakdown_b_price"] == 84_000.0
