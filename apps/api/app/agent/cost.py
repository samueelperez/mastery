"""Shared helpers for LLM usage / cost tracking.

Two layers:

1. :func:`extract_usage_and_cost` — pure function that parses a pydantic-ai
   ``AgentRunResult`` (or compatible object) into ``(usage_tokens, cost_usd)``.
   Tolerant of pydantic-ai API churn; falls back to ``(None, None)``.

2. :func:`persist_llm_usage` — best-effort write into ``llm_usage_log``.
   Never raises to the caller (a DB hiccup must not crash an agent run).

The pricing inputs reuse ``review_price_*`` settings as the per-million USD
rates. The main chat and scout agents currently inherit those rates;
M2 can split them per-source once we observe enough real cost data.

Streaming note: the main chat endpoint dispatches via ``VercelAIAdapter``
which yields an SSE response object before the agent run completes. Wiring
the usage capture there requires either (a) post-stream callback support
in the adapter, or (b) replacing the adapter with manual streaming +
capture. Until that lands, only reviewer / post-mortem / scout paths
(which call ``.run()`` and have the result in hand) write to the table.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings

LOG = structlog.get_logger("agent.cost")


def extract_usage_and_cost(
    result: Any,
    settings: Settings,
) -> tuple[dict[str, Any] | None, float | None]:
    """Best-effort extraction of token counts + estimated USD cost from a
    pydantic-ai run result. Returns ``(None, None)`` on any failure —
    callers should treat absence as "unknown" not "free".

    Pricing reuses the per-million-token rates configured for the reviewer
    (``review_price_input_per_m_usd`` etc.) since reviewer/post-mortem/
    scout all run against the same Anthropic model family. Override per
    source if/when M2 introduces source-specific rates.
    """
    try:
        usage = result.usage()
    except Exception:
        return None, None

    def _get(name: str) -> int:
        v = getattr(usage, name, 0)
        try:
            return int(v) if v is not None else 0
        except (TypeError, ValueError):
            return 0

    input_t = _get("input_tokens") or _get("request_tokens")
    output_t = _get("output_tokens") or _get("response_tokens")
    cache_read = _get("cache_read_input_tokens") or _get("cache_read_tokens")
    cache_write = _get("cache_write_input_tokens") or _get("cache_creation_tokens")

    usage_tokens: dict[str, Any] = {
        "input": input_t,
        "output": output_t,
        "cache_read": cache_read,
        "cache_create": cache_write,
        "total": input_t + output_t,
    }

    in_per_m = settings.review_price_input_per_m_usd
    out_per_m = settings.review_price_output_per_m_usd
    cache_per_m = settings.review_price_cache_read_per_m_usd

    chargeable_input = max(input_t - cache_read, 0)
    cost = (
        chargeable_input * in_per_m / 1_000_000
        + cache_read * cache_per_m / 1_000_000
        + output_t * out_per_m / 1_000_000
    )
    return usage_tokens, round(cost, 6)


async def persist_llm_usage(
    session: AsyncSession,
    *,
    user_id: str,
    source: str,
    model_id: str,
    usage_tokens: dict[str, Any] | None,
    cost_usd: float | None,
    request_id: str | None = None,
) -> None:
    """Insert one row into ``llm_usage_log``. Errors are absorbed.

    Args:
        session: caller-managed async session — the function does NOT
            commit; the caller's ``session_scope()`` block handles that.
        source: bounded enum at the application layer — one of
            ``chat`` / ``scout`` / ``review`` / ``post_mortem`` / ``audit``.
        request_id: optional correlation id (request UUID / chat turn id).
            Lets logs join to this row downstream.
    """
    try:
        await session.execute(
            text(
                """
                INSERT INTO llm_usage_log (
                    user_id, source, model_id,
                    usage_tokens, cost_usd, request_id
                ) VALUES (
                    :user_id, :source, :model_id,
                    CAST(:usage_tokens AS jsonb), :cost_usd, :request_id
                )
                """
            ),
            {
                "user_id": user_id,
                "source": source,
                "model_id": model_id,
                "usage_tokens": (
                    json.dumps(usage_tokens) if usage_tokens is not None else None
                ),
                "cost_usd": cost_usd,
                "request_id": request_id,
            },
        )
    except Exception as exc:  # pragma: no cover — defensive
        LOG.warning(
            "llm_usage_log.insert_failed",
            source=source,
            user_id=user_id,
            error=f"{type(exc).__name__}: {exc}",
        )
