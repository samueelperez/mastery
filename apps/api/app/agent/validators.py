"""Citation contract enforcement.

The blueprint principle is that the LLM must NEVER produce a number without
citing the tool that produced it. Two layers of enforcement:

1. **tool_name** discriminator — every citation must reference a tool the
   agent actually called this turn. (LLMs can't reliably echo opaque
   provider-generated IDs like `toolu_vrtx_018Mgk8rcfAiyZrB46vTzYKa`, so we
   match on the semantic function name instead of `tool_call_id`.)

2. **handle existence** — for citations whose snapshot references a stable
   handle (`run_id` from run_backtest/get_strategy_metrics, or `trade_id`
   from get_similar_past_trades / log_trade), the handle must appear in
   the tool's actual return value this turn. This blocks the failure mode
   where the LLM cites `tool_name="get_strategy_metrics"` with a fabricated
   `snapshot={"run_id": "<random-uuid>", ...}` that doesn't exist in the DB.

Violations raise `ModelRetry` so the agent re-attempts.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)

from app.agent.deps import AgentDeps
from app.agent.models import ToolCitation, TradeIdea


def _collect_tool_names(messages: list[ModelRequest | ModelResponse]) -> set[str]:
    names: set[str] = set()
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    names.add(part.tool_name)
    return names


# Keys that, in any tool output dict, name a stable handle the agent might
# legitimately cite. Walking the JSON and harvesting these gives us the set
# of handles that genuinely exist this turn.
_HANDLE_KEYS: tuple[str, ...] = ("run_id", "id", "last_run_id", "trade_id")
_HANDLE_LIST_KEYS: tuple[str, ...] = ("trade_ids",)


def _walk_handles(value: Any, sink: set[str]) -> None:
    """Recursively harvest handle-shaped strings from a tool return payload."""
    if isinstance(value, dict):
        for k, v in value.items():
            if k in _HANDLE_KEYS and isinstance(v, str) and v:
                sink.add(v)
            elif k in _HANDLE_LIST_KEYS and isinstance(v, list):
                for item in v:
                    if isinstance(item, str) and item:
                        sink.add(item)
            else:
                _walk_handles(v, sink)
    elif isinstance(value, list):
        for item in value:
            _walk_handles(item, sink)


def _collect_returned_handles(
    messages: list[ModelRequest | ModelResponse],
) -> set[str]:
    """Every run_id / trade_id / id string returned by a tool this turn."""
    handles: set[str] = set()
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    try:
                        payload = part.model_response_object()
                    except Exception:
                        continue
                    _walk_handles(payload, handles)
    return handles


def _cited_handles(c: ToolCitation) -> list[str]:
    """Pull any run_id / trade_id strings out of a citation snapshot."""
    out: list[str] = []
    snap = c.snapshot or {}
    for key in _HANDLE_KEYS:
        v = snap.get(key)
        if isinstance(v, str) and v:
            out.append(v)
    for key in _HANDLE_LIST_KEYS:
        v = snap.get(key)
        if isinstance(v, list):
            out.extend(item for item in v if isinstance(item, str) and item)
    return out


def register_validators(agent: Agent[AgentDeps, TradeIdea | str]) -> None:
    @agent.output_validator
    async def must_cite_quantitative_claims(
        ctx: RunContext[AgentDeps],
        output: TradeIdea | str,
    ) -> TradeIdea | str:
        # Free-text answers (definitional questions) bypass the citation check.
        if isinstance(output, str):
            return output

        used_tools = _collect_tool_names(list(ctx.messages))
        returned_handles = _collect_returned_handles(list(ctx.messages))

        def _check(label: str, cites: list[ToolCitation]) -> None:
            for c in cites:
                if c.tool_name not in used_tools:
                    raise ModelRetry(
                        f"{label} cites tool_name={c.tool_name!r}, which you did NOT call "
                        f"this turn. Tools you actually called: {sorted(used_tools)}. "
                        f"Either cite one of those or remove the field."
                    )
                # Layer 2: verify any handle the snapshot claims came from a tool.
                for handle in _cited_handles(c):
                    if handle not in returned_handles:
                        raise ModelRetry(
                            f"{label} cites a handle ({handle!r}) that no tool returned this "
                            f"turn. Available handles: {sorted(returned_handles) or 'none'}. "
                            f"Use only run_id / trade_id values that appear in tool outputs."
                        )

        # Numeric fields requiring citations.
        for label, value, cites in (
            ("entry", output.entry, output.entry_citations),
            ("invalidation", output.invalidation, output.invalidation_citations),
        ):
            if value is not None and not cites:
                raise ModelRetry(
                    f"`{label}={value}` requires at least one ToolCitation referencing a "
                    f"tool you actually called this turn (one of {sorted(used_tools)})."
                )
            _check(f"`{label}`", cites)

        for tgt in output.targets:
            if not tgt.citations:
                raise ModelRetry(
                    f"target {tgt.label}={tgt.price} requires at least one ToolCitation "
                    f"(tool_name from {sorted(used_tools)})."
                )
            _check(f"target {tgt.label}", tgt.citations)

        # Non-no_trade ideas need at least one Confluence with citations.
        if output.direction != "no_trade" and not output.confluences:
            raise ModelRetry(
                "Ideas with direction != 'no_trade' require at least one Confluence with citations. "
                "If higher-TF context doesn't justify a setup, set direction='no_trade'."
            )
        for conf in output.confluences:
            _check(f"confluence {conf.timeframe}", conf.citations)

        ctx.deps.log.info(
            "agent.output_validated",
            direction=output.direction,
            confidence=output.confidence,
            n_confluences=len(output.confluences),
            n_targets=len(output.targets),
            handles_returned=len(returned_handles),
        )
        return output
