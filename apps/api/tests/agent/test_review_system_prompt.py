"""Sanity check del system prompt del review_agent.

Lo importante para caching es que el contenido sea estable y el version tag
exista. Pinneamos los bloques mínimos para que un cambio accidental no rompa
el cache en producción sin que un test grite.
"""

from __future__ import annotations

from app.reviewer.system_prompt import (
    REVIEW_SYSTEM_PROMPT_VERSION,
    build_review_system_prompt,
)


def test_version_tag_present() -> None:
    s = build_review_system_prompt()
    assert REVIEW_SYSTEM_PROMPT_VERSION in s


def test_contains_required_sections() -> None:
    s = build_review_system_prompt()
    # Sanity: cada sección crítica nombrada al menos una vez. Si renames a
    # los bloques, este test te recuerda bumpear REVIEW_SYSTEM_PROMPT_VERSION.
    assert "Mission" in s
    assert "Citation contract" in s
    assert "Decision tree" in s
    assert "current_state" in s
    assert "recommendation" in s
    # Decision tree triggers list (deben estar todos para que el LLM sepa por
    # qué le llaman).
    for kind in [
        "entry_hit",
        "tp_partial",
        "time_elapsed",
        "price_move",
        "approaching_sl",
        "regime_change",
    ]:
        assert kind in s


def test_is_deterministic() -> None:
    """No timestamps, no UUIDs, no per-request data. El prompt es byte-stable
    entre llamadas para que Anthropic cachee la prefix."""
    a = build_review_system_prompt()
    b = build_review_system_prompt()
    assert a == b
