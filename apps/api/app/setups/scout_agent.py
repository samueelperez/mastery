"""Scout agent factory — dedicated Haiku 4.5 singleton.

The scout pipeline (``setups/scout_dispatcher.py``) historically reused the
main chat agent (Sonnet 4.6). The scout's decisions are higher frequency
(every alert-rule match potentially fires one invocation) and narrower in
scope (single-rule trigger → propose-or-skip), so Sonnet's cost / latency
is overkill. Haiku 4.5 is ~10× cheaper at this volume and still satisfies
the citation contract.

This module keeps the scout's agent independent so:

- the model can be swapped here without touching the main chat agent.
- the singleton's prompt cache is isolated — both agents would otherwise
  share the same cache key prefix (system prompt + tools catalog) and
  swapping the model invalidates whichever was warmer.
- ``get_scout_agent_async()`` provides a lock-protected cold-start path
  for the dispatcher (called from ``alerts/runtime.py`` lifespan).
"""

from __future__ import annotations

import asyncio

from pydantic_ai import Agent

from app.agent.agent import SCOUT_MODEL_ID, build_agent
from app.agent.deps import AgentDeps
from app.agent.models import BriefAnalysis, TradeIdea

_scout_instance: Agent[AgentDeps, BriefAnalysis | TradeIdea | str] | None = None
_scout_lock: asyncio.Lock | None = None


def _get_scout_lock() -> asyncio.Lock:
    global _scout_lock
    if _scout_lock is None:
        _scout_lock = asyncio.Lock()
    return _scout_lock


def get_scout_agent() -> Agent[AgentDeps, BriefAnalysis | TradeIdea | str]:
    """Sync accessor — preferred in the dispatcher hot path after the
    lifespan has triggered eager construction. For cold-start safety use
    :func:`get_scout_agent_async`."""
    global _scout_instance
    if _scout_instance is None:
        _scout_instance = build_agent(model_id=SCOUT_MODEL_ID)
    return _scout_instance


async def get_scout_agent_async() -> Agent[
    AgentDeps, BriefAnalysis | TradeIdea | str
]:
    """Lock-protected async accessor — use from dispatchers / lifespan code
    where a cold-start race between concurrent invocations is possible."""
    global _scout_instance
    if _scout_instance is not None:
        return _scout_instance
    async with _get_scout_lock():
        if _scout_instance is None:
            _scout_instance = build_agent(model_id=SCOUT_MODEL_ID)
        return _scout_instance
