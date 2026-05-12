"""Secondary agent: post-entry trade reviews.

Independiente del main agent (no extiende su `output_type` union) por tres
razones:
- System prompt distinto y ~5× más corto → mejor cache hit, no contamina el chat.
- Tools subset (~7 vs 14) → menos coste, menos alucinación de tool calls.
- Permite tunear modelo/thinking/max_tokens sin afectar el chat principal.

Invocado por `app.reviewer.dispatcher.maybe_run_review` cuando el
SetupRuntime detecta un trigger relevante. Output `TradeReview` se persiste
en `setup_reviews` + audit event en `setup_events`.
"""

from __future__ import annotations

from pydantic_ai import Agent
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider

from app.agent.deps import AgentDeps
from app.agent.models import TradeReview
from app.agent.tools.confluence import register_confluence_tools
from app.agent.tools.correlation import register_correlation_tool
from app.agent.tools.indicators import register_indicator_tools
from app.agent.tools.ohlcv import register_ohlcv_tools
from app.agent.tools.perps_data import register_perps_data_tools
from app.agent.tools.structure import register_structure_tools
from app.agent.tools.volume_profile import register_volume_profile_tool
from app.core.config import get_settings
from app.reviewer.system_prompt import build_review_system_prompt
from app.reviewer.validators import register_review_validators

# Mismo modelo que el main, pero con thinking más bajo y output cap menor.
# Una review es estructuralmente más simple que un TradeIdea completo.
REVIEW_MODEL_ID = "anthropic/claude-sonnet-4.6"


def build_review_agent() -> Agent[AgentDeps, TradeReview]:
    api_key = get_settings().openrouter_api_key
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set — review_agent cannot start."
        )
    model = OpenRouterModel(
        REVIEW_MODEL_ID,
        provider=OpenRouterProvider(api_key=api_key),
    )
    settings = OpenRouterModelSettings(
        # Lower cap: review schema is small, ~3-5 tool calls bastan.
        max_tokens=8000,
        # Lower thinking budget than main agent — la decisión es bounded.
        thinking="low",
        openrouter_usage={"include": True},
    )
    agent = Agent[AgentDeps, TradeReview](
        model,
        deps_type=AgentDeps,
        output_type=TradeReview,
        system_prompt=build_review_system_prompt(),
        model_settings=settings,
        retries=2,
    )
    # Tool subset (alfabético — cache prefix stability). Solo lo que una
    # review legítimamente necesita; NO incluye log_trade, run_backtest,
    # create_alert, journal_query, biases, walk_forward, cpcv ni
    # strategy_metrics — esas tools son para análisis nuevo, no para
    # decidir hold/partial/exit sobre un setup ya vivo.
    register_confluence_tools(agent)
    register_correlation_tool(agent)
    register_indicator_tools(agent)
    register_ohlcv_tools(agent)
    register_perps_data_tools(agent)
    register_structure_tools(agent)
    register_volume_profile_tool(agent)
    register_review_validators(agent)
    return agent


# Lazy singleton — created on first access so tests can patch before init.
_review_agent_instance: Agent[AgentDeps, TradeReview] | None = None


def get_review_agent() -> Agent[AgentDeps, TradeReview]:
    global _review_agent_instance
    if _review_agent_instance is None:
        _review_agent_instance = build_review_agent()
    return _review_agent_instance
