"""Agent factory + singleton.

Built once at module import so the system prompt + tool catalogue stay byte-stable
across requests (essential for prompt caching).
"""

from __future__ import annotations

import asyncio

from pydantic_ai import Agent
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider

from app.agent.deps import AgentDeps
from app.agent.models import BriefAnalysis, TradeIdea
from app.agent.system_prompt import build_system_blocks
from app.agent.tools.basis import register_basis_tool
from app.agent.tools.biases import register_bias_tool
from app.agent.tools.confluence import register_confluence_tools
from app.agent.tools.correlation import register_correlation_tool
from app.agent.tools.cpcv import register_cpcv_tool
from app.agent.tools.create_alert import register_create_alert_tool
from app.agent.tools.delete_alert import register_delete_alert_tool
from app.agent.tools.dominance import register_dominance_tool
from app.agent.tools.factor_stats import register_factor_stats_tool
from app.agent.tools.indicators import register_indicator_tools
from app.agent.tools.journal_query import register_journal_query_tool
from app.agent.tools.list_alerts import register_list_alerts_tool
from app.agent.tools.log_trade import register_log_trade_tool
from app.agent.tools.ohlcv import register_ohlcv_tools
from app.agent.tools.perps_data import register_perps_data_tools
from app.agent.tools.perps_dynamics import register_perps_dynamics_tool
from app.agent.tools.run_backtest import register_run_backtest_tool
from app.agent.tools.similar_setups import register_similar_setups_tool
from app.agent.tools.strategy_metrics import register_strategy_metrics_tool
from app.agent.tools.structure import register_structure_tools
from app.agent.tools.volume_profile import register_volume_profile_tool
from app.agent.tools.walk_forward import register_walk_forward_tool
from app.agent.validators import register_validators
from app.core.config import get_settings
from app.liquidation.tool import register_liquidation_tool

# Default model for chat. Deep-dive (Opus 4.7) is plumbed in but not toggled
# automatically in F1 — F2 will surface a UI selector.
DEFAULT_MODEL_ID = "anthropic/claude-sonnet-4.6"

# Scout model (Cerebro 1 — Day 5+). Haiku 4.5 is ~10× cheaper than Sonnet 4.6
# for the scout's narrow use case (single-rule trigger → propose/skip). Cost
# matters here because scouts can fire dozens of times per day across alerts.
SCOUT_MODEL_ID = "anthropic/claude-haiku-4.5"


def build_agent(
    model_id: str = DEFAULT_MODEL_ID,
) -> Agent[AgentDeps, BriefAnalysis | TradeIdea | str]:
    api_key = get_settings().openrouter_api_key
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. Add it to apps/api/.env "
            "(get a key at https://openrouter.ai/keys)."
        )
    # Explicit provider so the key flows from Settings (which read from .env),
    # not from os.environ — the latter would only work when uvicorn auto-loads it.
    model = OpenRouterModel(
        model_id,
        provider=OpenRouterProvider(api_key=api_key),
    )
    cfg = get_settings()
    settings = OpenRouterModelSettings(
        # Audit fix 2026-05: promovido a Settings (`AGENT_MAX_TOKENS`,
        # `AGENT_THINKING`, `AGENT_RETRIES`) para tunear sin re-deploy.
        # Defaults: max_tokens=24000 (cubre retry del validator sin truncar),
        # thinking="medium" (Anthropic adaptive), retries=2.
        max_tokens=cfg.agent_max_tokens,
        thinking=cfg.agent_thinking,
        # Keep usage details in the response so we can audit cache hits later.
        openrouter_usage={"include": True},
    )
    agent = Agent[AgentDeps, BriefAnalysis | TradeIdea | str](
        model,
        deps_type=AgentDeps,
        output_type=BriefAnalysis | TradeIdea | str,  # type: ignore[arg-type]
        system_prompt=build_system_blocks(),
        model_settings=settings,
        retries=cfg.agent_retries,
    )
    # Tools registered in alphabetical order for cache-prefix stability with
    # Anthropic. CLAUDE.md invariant. NO reorganizar sin medir el cache hit
    # rate post-deploy.
    register_basis_tool(agent)
    register_bias_tool(agent)
    register_confluence_tools(agent)
    register_correlation_tool(agent)
    register_cpcv_tool(agent)
    register_create_alert_tool(agent)
    register_delete_alert_tool(agent)
    register_dominance_tool(agent)
    register_factor_stats_tool(agent)
    register_indicator_tools(agent)
    register_journal_query_tool(agent)
    register_liquidation_tool(agent)
    register_list_alerts_tool(agent)
    register_log_trade_tool(agent)
    register_ohlcv_tools(agent)
    register_perps_data_tools(agent)
    register_perps_dynamics_tool(agent)
    register_run_backtest_tool(agent)
    register_similar_setups_tool(agent)
    register_strategy_metrics_tool(agent)
    register_structure_tools(agent)
    register_volume_profile_tool(agent)
    register_walk_forward_tool(agent)
    register_validators(agent)
    return agent


# Lazy singleton — created on first access. Protegido por `asyncio.Lock` para
# evitar race entre 4 callers concurrentes en cold-start (chat, scout,
# reviewer, post_mortem) — audit fix 2026-05.
_agent_instance: Agent[AgentDeps, BriefAnalysis | TradeIdea | str] | None = None
_agent_lock: asyncio.Lock | None = None


def _get_agent_lock() -> asyncio.Lock:
    global _agent_lock
    if _agent_lock is None:
        _agent_lock = asyncio.Lock()
    return _agent_lock


def get_agent() -> Agent[AgentDeps, BriefAnalysis | TradeIdea | str]:
    """Sync accessor — preferido en hot path tras eager init en lifespan.
    Para garantías de no-race usar `await get_agent_async()` en cold path."""
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = build_agent()
    return _agent_instance


async def get_agent_async() -> Agent[AgentDeps, BriefAnalysis | TradeIdea | str]:
    """Async accessor con lock. Úsalo desde dispatchers en cold-start."""
    global _agent_instance
    if _agent_instance is not None:
        return _agent_instance
    async with _get_agent_lock():
        if _agent_instance is None:
            _agent_instance = build_agent()
        return _agent_instance
