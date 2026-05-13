"""Smoke + singleton tests for the dedicated scout agent (PR-07 / ADR-003).

The scout pipeline used to share the main chat agent (Sonnet 4.6); after
the migration it runs on Haiku 4.5 via its own pydantic-ai Agent
instance. These tests pin the wiring:

- ``SCOUT_MODEL_ID`` resolves to the Haiku OpenRouter id.
- ``get_scout_agent()`` returns a singleton bound to that model id.
- The scout agent is a different instance than the main chat agent.
"""

from __future__ import annotations

import os

import pytest

# Pydantic-ai's OpenRouterProvider raises at construction if no key is set,
# so we provide a placeholder before importing the modules under test.
os.environ.setdefault("OPENROUTER_API_KEY", "sk-test-placeholder")


def _reset_singletons() -> None:
    """Clear both the scout and main agent singletons so independent tests
    don't piggyback on a previously-built instance."""
    import app.agent.agent as agent_mod
    import app.setups.scout_agent as scout_mod

    agent_mod._agent_instance = None  # type: ignore[attr-defined]
    scout_mod._scout_instance = None  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _isolated_singletons():
    _reset_singletons()
    yield
    _reset_singletons()


def test_scout_model_id_is_haiku():
    from app.agent.agent import SCOUT_MODEL_ID

    assert SCOUT_MODEL_ID == "anthropic/claude-haiku-4.5"


def test_get_scout_agent_returns_haiku_singleton():
    from app.setups.scout_agent import get_scout_agent

    agent_a = get_scout_agent()
    agent_b = get_scout_agent()
    assert agent_a is agent_b
    assert agent_a.model.model_name == "anthropic/claude-haiku-4.5"


def test_scout_agent_is_distinct_from_main_chat_agent():
    """Scout and main share the same factory (``build_agent``) but each must
    have its own singleton — otherwise model swaps on one would invalidate
    the other's prompt cache."""
    from app.agent.agent import DEFAULT_MODEL_ID, get_agent
    from app.setups.scout_agent import get_scout_agent

    chat_agent = get_agent()
    scout_agent = get_scout_agent()
    assert chat_agent is not scout_agent
    assert chat_agent.model.model_name == DEFAULT_MODEL_ID
    assert scout_agent.model.model_name != chat_agent.model.model_name


async def test_get_scout_agent_async_returns_same_singleton():
    from app.setups.scout_agent import get_scout_agent, get_scout_agent_async

    a = await get_scout_agent_async()
    b = get_scout_agent()
    assert a is b
