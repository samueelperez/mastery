"""Aggregator service that merges provider outputs into HeatmapSnapshots.

Day 1: stub. Implemented in full on Day 4 per
`docs/specs/liquidation/04_HEATMAP_SERVICE.md`.
"""

from __future__ import annotations


class HeatmapService:
    """Aggregates `ProviderHeatmap` outputs into `HeatmapSnapshot` with
    weighted merging, agreement computation, and persistence."""
