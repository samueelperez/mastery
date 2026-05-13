"""pydantic-ai tool registration for the liquidation heatmap engine.

Exposes a single tool: `get_liquidation_heatmap`. All other module APIs are
private; the agent's only entry point is via this tool.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic_ai import Agent, RunContext

from app.agent.deps import AgentDeps
from app.agent.tools._envelope import ToolResult
from app.core.exchanges.binance_adapter import BinanceAdapter
from app.core.exchanges.exchange_context import ExchangeContext
from app.liquidation.models import HeatmapSnapshot
from app.liquidation.providers._hyperliquid_client import HyperliquidClient
from app.liquidation.providers.derived import DerivedLiquidationProvider
from app.liquidation.providers.hyperliquid import HyperliquidLiquidationProvider
from app.liquidation.repo import LiquidationRepo
from app.liquidation.service import HeatmapService

LOG = logging.getLogger(__name__)


def register_liquidation_tool(agent: Agent) -> None:
    """Register the get_liquidation_heatmap tool on the given agent.

    Called by `build_agent()` (main copilot) and `build_review_agent()`
    (supervisor). Both share AgentDeps and the same tool surface.

    Lazy singleton HyperliquidClient lives in a closure so it's reused across
    invocations. The client owns an httpx.AsyncClient pool — reusing it
    avoids the per-call socket-open overhead.
    """
    _hl_client_holder: dict[str, HyperliquidClient] = {}

    def _get_hl_client() -> HyperliquidClient:
        if "client" not in _hl_client_holder:
            _hl_client_holder["client"] = HyperliquidClient()
        return _hl_client_holder["client"]

    @agent.tool
    async def get_liquidation_heatmap(
        ctx: RunContext[AgentDeps],
        symbol: str,
        timeframe: Literal["1h", "4h", "1d"] = "4h",
        max_distance_pct: float = 10.0,
    ) -> ToolResult[HeatmapSnapshot]:
        """Get current liquidation heatmap for symbol at given timeframe.

        Returns magnet zones (long_liq and short_liq) ordered by distance to
        current price. Each zone includes est_volume_usd, source_breakdown,
        and confidence derived from cross-provider agreement.

        USE THIS WHEN:
        - Proposing a directional setup: cite nearest_short_liq as TP target
          for a long setup, nearest_long_liq as TP target for a short setup.
        - Evaluating SL placement: avoid placing SL right inside a same-side
          magnet zone (it WILL get hit by the cascade).
        - Assessing setup quality: imbalance_ratio > 1.5 favors counter-trend
          (long-heavy market, more fuel for downside flush).
        - Sizing a setup: cluster_density > 0.5 means strong short-term
          magnet effect, can support more aggressive entry.

        DO NOT USE THIS TO:
        - Predict price direction (zones are conditional magnets, not signals).
        - Cite zones beyond max_distance_pct (irrelevant for setups with
          short-term horizon).
        - Use as TP target for a same-side zone (illogical: if you're long,
          your TP is where SHORTS get liquidated, not longs).

        Args:
            symbol: 'BTCUSDT', 'ETHUSDT', or 'SOLUSDT'.
            timeframe: '1h', '4h', or '1d'. Affects lookback window for the
                derived provider; does not change which positions exist
                on-chain.
            max_distance_pct: Cap zones at ±this % from current price.
                Default 10%; use 5% for tight scalping setups, 15% for
                swing setups.
        """
        log = ctx.deps.log.bind(
            tool="get_liquidation_heatmap",
            symbol=symbol,
            timeframe=timeframe,
        )

        # Resolve current price via the exchange adapter. M2: pre-build the
        # adapter at lifespan and share via AgentDeps.
        adapter = BinanceAdapter(ctx=ExchangeContext.MAINNET_RO)
        try:
            ticker = await adapter.fetch_ticker(symbol)
            current_price = float(ticker["last"])
        finally:
            await adapter.close()

        async with ctx.deps.session_factory() as session:
            repo = LiquidationRepo(session=session, user_id=ctx.deps.user_id)
            providers = [
                DerivedLiquidationProvider(ctx.deps.session_factory),
                HyperliquidLiquidationProvider(ctx.deps.session_factory, _get_hl_client()),
            ]
            service = HeatmapService(providers=providers, repo=repo)
            snapshot = await service.get_snapshot(
                symbol=symbol,
                timeframe=timeframe,
                current_price=current_price,
                max_distance_pct=max_distance_pct,
            )

        log.info(
            "heatmap_returned",
            zones=len(snapshot.magnet_zones),
            agreement=snapshot.sources_agreement,
            sources=snapshot.sources_used,
            warnings=snapshot.provenance.warnings,
        )

        return ToolResult(data=snapshot, provenance=snapshot.provenance)
