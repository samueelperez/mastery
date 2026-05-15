# 05 — Agent Tool and Citation Validator

<context>
This spec exposes the liquidation engine to the orchestrator agent as a pydantic-ai tool. It also extends the citation contract validator in `agent/validators.py` to enforce that any claim citing this tool must trace to a real magnet zone with numerical agreement.

The tool is registered alphabetically in both `agent/agent.py::build_agent()` (main copilot) and `reviewer/agent.py::build_review_agent()` (supervisor that fuses reviewer + post_mortem). Scout uses Haiku — we register the same tool there too, but Haiku will rarely invoke it on its own; it's more often invoked in chat/supervisor contexts.
</context>

<deliverables>
- `apps/api/app/liquidation/tool.py` — pydantic-ai tool registration function.
- Modifications to `apps/api/app/agent/agent.py` to call `register_liquidation_tool(agent)` alphabetically.
- Modifications to `apps/api/app/reviewer/agent.py` to also register it.
- Modifications to `apps/api/app/agent/validators.py` to extend citation checks.
- Modifications to `apps/api/app/agent/system_prompt.py` — add the tool description to `TOOLS_CATALOG` in alphabetical order.
- `apps/api/tests/liquidation/test_tool.py` — tool behavior with mocked service.
- Additions to `apps/api/tests/agent/test_validators_citation_rigor.py` — 4 new tests for this tool.
</deliverables>

<file_apps_api_app_liquidation_tool_py>

```python
"""pydantic-ai tool registration for the liquidation heatmap engine.

Exposes a single tool: `get_liquidation_heatmap`. The tool is the only entry
point through which the agent accesses this module. All other internal APIs
are private to the module.
"""
from __future__ import annotations

import logging
from typing import Literal

from pydantic_ai import Agent, RunContext

from app.agent.deps import AgentDeps
from app.agent.tools._envelope import ToolResult
from app.core.exchanges.binance_adapter import BinanceAdapter, ExchangeContext
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

    The tool factory holds a single HyperliquidClient via closure so it's
    reused across invocations. The client owns an httpx.AsyncClient pool.
    """
    # Lazy singleton across invocations within one agent process.
    _hyperliquid_client: dict[str, HyperliquidClient] = {}

    def _get_hl_client() -> HyperliquidClient:
        if "client" not in _hyperliquid_client:
            _hyperliquid_client["client"] = HyperliquidClient()
        return _hyperliquid_client["client"]

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
            timeframe: '1h', '4h', or '1d'. Affects lookback window for
                derived provider; does not change which positions exist
                on-chain.
            max_distance_pct: Cap zones at ±this % from current price.
                Default 10%; use 5% for tight scalping setups, 15% for
                swing setups.
        """
        log = ctx.deps.log.bind(tool="get_liquidation_heatmap", symbol=symbol, timeframe=timeframe)

        # Resolve current price via the exchange adapter.
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

        return ToolResult(
            data=snapshot,
            provenance=snapshot.provenance,
        )
```
</file_apps_api_app_liquidation_tool_py>

<system_prompt_addition>

In `apps/api/app/agent/system_prompt.py`, the `TOOLS_CATALOG` block is a frozen string registered in alphabetical order. Insert this entry between `get_indicators` and `get_market_dominance` (alphabetical position for "get_liquidation_heatmap"):

```
- get_liquidation_heatmap(symbol, timeframe='4h', max_distance_pct=10.0):
    Returns the current liquidation heatmap. Use this to identify magnet
    zones where leveraged positions get liquidated. Each zone carries
    est_volume_usd, distance_pct, side ('long_liq' = longs get liquidated
    here = price below current = potential support failure; 'short_liq' =
    shorts get liquidated = price above current = potential resistance
    break). For a directional setup, cite nearest_short_liq as TP for longs
    and nearest_long_liq as TP for shorts. NEVER cite same-side zones as
    TP — it's illogical. imbalance_ratio > 1.5 = long-heavy, favors
    counter-trend short. sources_agreement < 0.6 = degraded data, lower
    your confidence accordingly.
```

DO NOT also add separate datetime or timestamp data to TOOLS_CATALOG — system prompt MUST be byte-stable. The tool result is what carries per-call data.
</system_prompt_addition>

<validator_extension>

Open `apps/api/app/agent/validators.py`. Locate the `_validate_citations` function (or wherever the citation contract is enforced). Add the following check to the per-citation loop:

```python
# Inside the existing loop that validates each citation in TradeIdea.
if cit.tool_name == "get_liquidation_heatmap":
    snap = cit.snapshot or {}
    # The snapshot must contain at minimum: symbol, current_price,
    # sources_agreement, sources_used.
    required = {"symbol", "current_price", "sources_agreement", "sources_used"}
    missing = required - snap.keys()
    if missing:
        raise ModelRetry(
            f"Citation to get_liquidation_heatmap is missing required keys: {missing}. "
            f"Re-cite with the full snapshot."
        )

    # The actual tool output for this turn must exist.
    real_outputs = _outputs_by_tool.get("get_liquidation_heatmap", [])
    if not real_outputs:
        raise ModelRetry(
            "TradeIdea cites get_liquidation_heatmap but the tool was not invoked "
            "this turn. Invoke the tool first, then cite its real output."
        )

    # Find a matching real snapshot (same symbol).
    matching = [r for r in real_outputs if r.data.symbol == snap.get("symbol")]
    if not matching:
        raise ModelRetry(
            f"Citation references symbol {snap.get('symbol')!r} but no tool call "
            f"this turn returned that symbol."
        )

    real = matching[0].data
    # Numerical match on current_price within 0.5% (price moves; not strict).
    real_px = real.current_price
    cit_px = float(snap["current_price"])
    if abs(cit_px - real_px) / real_px > 0.005:
        raise ModelRetry(
            f"current_price in citation ({cit_px}) deviates >0.5% from real "
            f"({real_px}). Re-cite using the most recent tool result."
        )

    # sources_agreement match exactly (it's deterministic per call).
    cit_agree = float(snap["sources_agreement"])
    if abs(cit_agree - real.sources_agreement) > 0.001:
        raise ModelRetry(
            f"sources_agreement in citation ({cit_agree}) does not match "
            f"the real value ({real.sources_agreement})."
        )

    # If TradeIdea is directional, check the zone semantics.
    if hasattr(idea, "direction") and idea.direction in ("long", "short"):
        # For long: TP zones should be short_liq (above price).
        # For short: TP zones should be long_liq (below price).
        # We can't read TP citation directly here; this check is for the
        # nearest zones referenced in this citation.
        if "nearest_short_liq_price" in snap and idea.direction == "short":
            raise ModelRetry(
                "TradeIdea direction is 'short' but citation references "
                "nearest_short_liq as a relevant zone. For a short setup, "
                "the relevant TP zone is nearest_long_liq (below current price)."
            )
        if "nearest_long_liq_price" in snap and idea.direction == "long":
            raise ModelRetry(
                "TradeIdea direction is 'long' but citation references "
                "nearest_long_liq as a relevant zone. For a long setup, "
                "the relevant TP zone is nearest_short_liq (above current price)."
            )

    # Confidence must be coherent with agreement.
    if cit_agree < 0.60 and idea.confidence == "high":
        raise ModelRetry(
            f"Citation has sources_agreement={cit_agree} but TradeIdea claims "
            f"confidence='high'. Low agreement requires confidence in {{'low','medium'}}."
        )
```

**`_outputs_by_tool` is the dict you already build in the existing validator code** (every tool output collected for the current turn, keyed by tool name). If your existing implementation doesn't track it that way, add it as part of this PR. This is mentioned in the existing module doc `agent.md` under "validators".
</validator_extension>

<agent_registration>

In `apps/api/app/agent/agent.py::build_agent()`, the tool registry block looks like (paraphrased from `agent.md`):

```python
agent = Agent(...)
register_basis_tool(agent)
register_bias_tool(agent)
# ... alphabetical order ...
register_indicator_tools(agent)
register_journal_query_tool(agent)
register_liquidation_tool(agent)   # <-- ADD HERE (between journal_query and list_alerts)
register_list_alerts_tool(agent)
# ...
```

Same change in `apps/api/app/reviewer/agent.py::build_review_agent()`. The supervisor agent (fused reviewer + post_mortem) uses a subset of tools but should include this one — magnet zone awareness is critical for SL tightening recommendations.

DO NOT register on the scout agent (Haiku 4.5) for M1. Scout is binary-decision and reading the heatmap is the orchestrator's job. We can revisit in M2 if needed.
</agent_registration>

<gotchas>
- The tool description in the docstring IS the contract the LLM reads. Treat changes as semver. If you ever rewrite, communicate the change in PR description.
- `BinanceAdapter(ctx=ExchangeContext.MAINNET_RO)` opens an httpx client per call. This is inefficient for a hot tool. Better pattern: pass the adapter into `AgentDeps` at lifespan start. For M1 the per-call cost is tolerable; flag as a refactor in M2.
- `HyperliquidClient` is held in a closure dict to share across invocations. This works because pydantic-ai constructs the agent once and the closure persists. Verify by running `pytest` with parallel workers — if the closure leaks between agent instances, refactor to module-level lazy singleton.
- `await adapter.close()` MUST be in a `finally` block. Leaks an httpx connection otherwise.
- `ToolResult.provenance` is set to `snapshot.provenance` directly — they're the same source. Don't double-wrap.
- The validator extension assumes `_outputs_by_tool` exists. If it doesn't, you need to wire it up. Look for `tool_call_id` to `output` mapping in the existing validator (probably called something like `_collect_tool_outputs`).
- The directional zone check is conservative: it catches the most common LLM mistake ("I'm short, I'll TP at the short_liq zone"). It does NOT catch every possible misuse — e.g. the LLM could cite a same-side zone as INVALIDATION reasoning, which is actually correct usage. Don't over-constrain.
- Test that `ModelRetry` is raised, not generic ValidationError — pydantic-ai handles `ModelRetry` with retry; generic exceptions abort.
</gotchas>

<acceptance>
- [ ] `register_liquidation_tool(agent)` is called in `build_agent()` and `build_review_agent()`, alphabetically.
- [ ] `TOOLS_CATALOG` in `system_prompt.py` contains the new tool description in alphabetical position. System prompt is still byte-stable (no per-request interpolation added).
- [ ] Calling the tool from a real agent run returns a `ToolResult[HeatmapSnapshot]`.
- [ ] The validator rejects a phantom zone citation (test `test_validator_rejects_phantom_liquidation_zone`).
- [ ] The validator rejects mismatched `sources_agreement` (test `test_validator_rejects_mismatched_agreement`).
- [ ] The validator rejects `confidence='high'` with `sources_agreement < 0.60` (test `test_validator_rejects_incoherent_confidence_liquidation`).
- [ ] The validator rejects same-side TP zone references (test `test_validator_rejects_same_side_tp_liquidation`).
- [ ] No new dependencies in `pyproject.toml`.
</acceptance>
