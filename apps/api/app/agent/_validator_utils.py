"""Helpers compartidos entre `agent/validators.py`, `reviewer/validators.py`
y `post_mortem/validators.py`.

Audit fix 2026-05: antes existían 3 copias de `_collect_tool_names` con la
misma firma — extraídas aquí para que un cambio en pydantic-AI (e.g.
nueva ToolPartKind) solo se atienda en un sitio.
"""

from __future__ import annotations

from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart


def collect_tool_names(messages: list[ModelRequest | ModelResponse]) -> set[str]:
    """Set of `tool_name`s invoked en este turno de conversación.

    Usado por los output_validators para verificar que cada citation del
    LLM referencia una tool realmente llamada (no inventada).
    """
    names: set[str] = set()
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    names.add(part.tool_name)
    return names


__all__ = ["collect_tool_names"]
