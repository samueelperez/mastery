"""Tercer agente independiente: análisis post-mortem de setups cerrados.

Distinto del main agent y del review_agent por:
- Output_type `PostMortem` específico (no entra en la union del main, no
  contamina el output_type del review).
- Subset de tools que EXCLUYE explícitamente `get_multi_tf_confluence`
  (está siendo auditado — circular si lo cita) y todos los mutadores
  (log_trade, create/delete_alert, run_backtest, etc.).
- `thinking="medium"` (no "low" como review) — necesita razonar
  contrafactuales del estilo "qué habría pasado si...".
- System prompt FROZEN y compacto (~500 tokens) para cache hot.

Invocado por `app.post_mortem.dispatcher.maybe_run_post_mortem`
cuando `setup_runtime` detecta SL hit o TP-all hit. Output `PostMortem` se
persiste en `setup_post_mortems` + audit event 'review_generated' en
`setup_events`.
"""

from __future__ import annotations

from pydantic_ai import Agent
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider

from app.agent.deps import AgentDeps
from app.agent.models import PostMortem
from app.agent.tools.factor_stats import register_factor_stats_tool
from app.agent.tools.indicators import register_indicator_tools
from app.agent.tools.journal_query import register_journal_query_tool
from app.agent.tools.ohlcv import register_ohlcv_tools
from app.agent.tools.perps_data import register_perps_data_tools
from app.agent.tools.structure import register_structure_tools
from app.agent.tools.volume_profile import register_volume_profile_tool
from app.core.config import get_settings
from app.post_mortem.system_prompt import build_post_mortem_system_prompt
from app.post_mortem.validators import register_post_mortem_validators

POST_MORTEM_MODEL_ID = "anthropic/claude-sonnet-4.6"


def build_post_mortem_agent() -> Agent[AgentDeps, PostMortem]:
    api_key = get_settings().openrouter_api_key
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set — post_mortem_agent cannot start."
        )
    model = OpenRouterModel(
        POST_MORTEM_MODEL_ID,
        provider=OpenRouterProvider(api_key=api_key),
    )
    settings = OpenRouterModelSettings(
        # Output cap modesto: PostMortem schema es bounded. 6k cubre 1 retry.
        max_tokens=6000,
        # Razonamiento contrafactual ("y si SL hubiera estado en X") no se
        # resuelve con thinking=low; subimos a medium.
        thinking="medium",
        openrouter_usage={"include": True},
    )
    agent = Agent[AgentDeps, PostMortem](
        model,
        deps_type=AgentDeps,
        output_type=PostMortem,
        system_prompt=build_post_mortem_system_prompt(),
        model_settings=settings,
        retries=2,
    )
    # Tool subset alfabético (cache prefix stability). EXCLUIDAS:
    # - confluence: auditándolo — circular.
    # - correlation, biases: irrelevantes para audit terminal.
    # - run_backtest, walk_forward, cpcv, strategy_metrics: análisis prospec-
    #   tivo, no retrospectivo del trade concreto.
    # - log_trade, create/delete_alert, list_alerts: mutadores o irrelevantes.
    register_factor_stats_tool(agent)
    register_indicator_tools(agent)
    register_journal_query_tool(agent)
    register_ohlcv_tools(agent)
    register_perps_data_tools(agent)
    register_structure_tools(agent)
    register_volume_profile_tool(agent)
    register_post_mortem_validators(agent)
    return agent


_post_mortem_agent_instance: Agent[AgentDeps, PostMortem] | None = None


def get_post_mortem_agent() -> Agent[AgentDeps, PostMortem]:
    global _post_mortem_agent_instance
    if _post_mortem_agent_instance is None:
        _post_mortem_agent_instance = build_post_mortem_agent()
    return _post_mortem_agent_instance
