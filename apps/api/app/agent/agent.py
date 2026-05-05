"""Agent factory + singleton.

Built once at module import so the system prompt + tool catalogue stay byte-stable
across requests (essential for prompt caching).
"""

from __future__ import annotations

from pydantic_ai import Agent
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider

from app.agent.deps import AgentDeps
from app.agent.models import BriefAnalysis, TradeIdea
from app.agent.system_prompt import build_system_blocks
from app.agent.tools.biases import register_bias_tool
from app.agent.tools.confluence import register_confluence_tools
from app.agent.tools.correlation import register_correlation_tool
from app.agent.tools.cpcv import register_cpcv_tool
from app.agent.tools.create_alert import register_create_alert_tool
from app.agent.tools.delete_alert import register_delete_alert_tool
from app.agent.tools.indicators import register_indicator_tools
from app.agent.tools.journal_query import register_journal_query_tool
from app.agent.tools.list_alerts import register_list_alerts_tool
from app.agent.tools.log_trade import register_log_trade_tool
from app.agent.tools.ohlcv import register_ohlcv_tools
from app.agent.tools.perps_data import register_perps_data_tools
from app.agent.tools.run_backtest import register_run_backtest_tool
from app.agent.tools.strategy_metrics import register_strategy_metrics_tool
from app.agent.tools.structure import register_structure_tools
from app.agent.tools.volume_profile import register_volume_profile_tool
from app.agent.tools.walk_forward import register_walk_forward_tool
from app.agent.validators import register_validators
from app.config import get_settings

# Default model for chat. Deep-dive (Opus 4.7) is plumbed in but not toggled
# automatically in F1 — F2 will surface a UI selector.
DEFAULT_MODEL_ID = "anthropic/claude-sonnet-4.6"


def build_agent() -> Agent[AgentDeps, BriefAnalysis | TradeIdea | str]:
    api_key = get_settings().openrouter_api_key
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. Add it to apps/api/.env "
            "(get a key at https://openrouter.ai/keys)."
        )
    # Explicit provider so the key flows from Settings (which read from .env),
    # not from os.environ — the latter would only work when uvicorn auto-loads it.
    model = OpenRouterModel(
        DEFAULT_MODEL_ID,
        provider=OpenRouterProvider(api_key=api_key),
    )
    settings = OpenRouterModelSettings(
        # 8000 cortaba durante reasoning antes de emitir final_result; 16k
        # cubría primer intento pero el RETRY del validator (cuando rebota
        # summary_es por longitud, p.ej.) inyecta otro reasoning + un nuevo
        # final_result que se trunca en streaming. 24k da headroom para 1
        # retry completo. Sonnet 4.6 soporta hasta 64k de output; subimos
        # selectivamente porque max_tokens escala coste/latencia.
        max_tokens=24000,
        # Cross-provider thinking knob; Pydantic AI maps "medium" to Anthropic
        # adaptive thinking with effort=medium when targeting a Claude model.
        thinking="medium",
        # Keep usage details in the response so we can audit cache hits later.
        openrouter_usage={"include": True},
    )
    agent = Agent[AgentDeps, BriefAnalysis | TradeIdea | str](
        model,
        deps_type=AgentDeps,
        output_type=BriefAnalysis | TradeIdea | str,  # type: ignore[arg-type]
        system_prompt=build_system_blocks(),
        model_settings=settings,
        retries=2,  # ModelRetries the validator can trigger before giving up
    )
    register_ohlcv_tools(agent)
    register_indicator_tools(agent)
    register_structure_tools(agent)
    register_confluence_tools(agent)
    register_correlation_tool(agent)
    register_perps_data_tools(agent)
    register_volume_profile_tool(agent)
    register_log_trade_tool(agent)
    register_journal_query_tool(agent)
    register_bias_tool(agent)
    register_run_backtest_tool(agent)
    register_walk_forward_tool(agent)
    register_cpcv_tool(agent)
    register_strategy_metrics_tool(agent)
    register_create_alert_tool(agent)
    register_list_alerts_tool(agent)
    register_delete_alert_tool(agent)
    register_validators(agent)
    return agent


# Lazy singleton — created on first access so test imports don't require a key.
_agent_instance: Agent[AgentDeps, BriefAnalysis | TradeIdea | str] | None = None


def get_agent() -> Agent[AgentDeps, BriefAnalysis | TradeIdea | str]:
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = build_agent()
    return _agent_instance
