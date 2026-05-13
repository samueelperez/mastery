"""Factor-snapshot enrichment for Cerebro 1.

When the agent emits a TradeIdea that cites ``get_liquidation_heatmap``,
the persisted ``journal_trades.factor_snapshot`` must include the cited
zone data so:

- ``liquidation/telegram_handlers.py::record_ground_truth`` can compute
  ``delta_a_pct`` / ``delta_b_pct`` for ``liquidation_agreement_log``.
- the weekly calibration job (``compute_provider_weights``) has the
  per-provider price signal it needs to update weights in M2.

This module is the bridge from the LLM's citation snapshot (which only
carries the citation-contract fields enforced by ``_verify_liquidation_citation``)
to the enriched dict written under ``factor_snapshot['get_liquidation_heatmap']``.

The helpers are pure functions over ``(output, messages)``; side-effect
free so they can be tested in isolation and called from the validator
without any DB / network dependency.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai.messages import ModelRequest, ModelResponse, ToolReturnPart


def find_heatmap_citation_snapshot(output: Any) -> dict[str, Any] | None:
    """Return a COPY of the snapshot of the first ``get_liquidation_heatmap``
    citation found on a TradeIdea (or None).

    Slots scanned, in order:

    - ``entry_citations``
    - ``stop_loss_citations``
    - ``targets[].citations``
    - ``invalidation_conditions[].citations``

    A copy is returned so the caller can augment the dict without mutating
    the source TradeIdea.
    """
    pools: list[list[Any]] = [
        list(getattr(output, "entry_citations", None) or []),
        list(getattr(output, "stop_loss_citations", None) or []),
    ]
    for tgt in getattr(output, "targets", None) or []:
        pools.append(list(getattr(tgt, "citations", None) or []))
    for cond in getattr(output, "invalidation_conditions", None) or []:
        pools.append(list(getattr(cond, "citations", None) or []))

    for citations in pools:
        for cit in citations:
            if getattr(cit, "tool_name", None) == "get_liquidation_heatmap":
                snap = getattr(cit, "snapshot", None)
                if isinstance(snap, dict) and snap:
                    return dict(snap)
    return None


def enrich_with_provider_breakdown(
    citation_snap: dict[str, Any],
    messages: list[ModelRequest | ModelResponse],
) -> dict[str, Any]:
    """Augment a heatmap citation snapshot with per-provider price breakdown
    and timeframe by reading the latest matching ``get_liquidation_heatmap``
    ToolReturnPart in ``messages``.

    Mutates and returns the input dict. Safe to call when no matching tool
    output exists — the snapshot is returned unchanged.

    Keys added when derivable:

    - ``source_breakdown_a_price``: float — A_derived provider's price for
      the cited zone (the cited price itself when A's bucket volume > 0;
      absent otherwise).
    - ``source_breakdown_b_price``: float — same for B_hyperliquid.
    - ``timeframe``: str — heatmap timeframe from the tool result.

    Rationale on the per-provider prices: when a provider contributes
    non-zero volume to the merged zone the agent cited, that provider
    "agrees" with the zone — its delta vs the proposed price is by
    construction 0 (the merged zone IS the consensus across contributors).
    When a provider contributes zero volume, there is no provider-side
    zone there, so we leave the field absent and the calibration job
    treats it as no-sample for that provider on this row.
    """
    cited_symbol = citation_snap.get("symbol")
    payload: dict[str, Any] | None = None
    # Iterate forward keeping the last match — the agent may call the
    # heatmap tool more than once per turn (e.g. for different timeframes);
    # the latest reflects the data the citation was most likely emitted
    # against.
    for msg in messages:
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if not isinstance(part, ToolReturnPart):
                continue
            if part.tool_name != "get_liquidation_heatmap":
                continue
            try:
                p = part.model_response_object()
            except Exception:
                continue
            if not isinstance(p, dict):
                continue
            data = p.get("data")
            if isinstance(data, dict) and data.get("symbol") == cited_symbol:
                payload = data

    if payload is None:
        return citation_snap

    tf = payload.get("timeframe")
    if isinstance(tf, str):
        citation_snap["timeframe"] = tf

    # Identify which side the citation referenced. Per the citation
    # contract enforced by ``_verify_liquidation_citation``, at most one of
    # ``nearest_*_liq_price`` is present per citation.
    if "nearest_short_liq_price" in citation_snap:
        cited_zone_key = "nearest_short_liq"
        cited_price_raw = citation_snap.get("nearest_short_liq_price")
    elif "nearest_long_liq_price" in citation_snap:
        cited_zone_key = "nearest_long_liq"
        cited_price_raw = citation_snap.get("nearest_long_liq_price")
    else:
        # Agreement-only citation (e.g. on entry, referencing
        # sources_agreement without a specific zone). Timeframe is the
        # only enrichment we can offer.
        return citation_snap

    zone = payload.get(cited_zone_key)
    if not isinstance(zone, dict):
        return citation_snap

    try:
        cited_price = float(cited_price_raw) if cited_price_raw is not None else None
    except (TypeError, ValueError):
        cited_price = None
    if cited_price is None or cited_price <= 0:
        return citation_snap

    breakdown = zone.get("source_breakdown") or {}
    if not isinstance(breakdown, dict):
        return citation_snap

    for prov_key, snap_key in (
        ("A_derived", "source_breakdown_a_price"),
        ("B_hyperliquid", "source_breakdown_b_price"),
    ):
        try:
            vol = float(breakdown.get(prov_key, 0) or 0)
        except (TypeError, ValueError):
            vol = 0.0
        if vol > 0:
            citation_snap[snap_key] = cited_price

    return citation_snap
