"""Citation contract enforcement.

The blueprint principle is that the LLM must NEVER produce a number without
citing the tool that produced it. This is enforced as a Pydantic AI output
validator: any TradeIdea with an entry/invalidation/target price but no
citations triggers a `ModelRetry`, forcing the agent to either cite the
relevant tool_call_id or downgrade to direction='no_trade' / free text.
"""

from __future__ import annotations

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart

from app.agent.deps import AgentDeps
from app.agent.models import ToolCitation, TradeIdea


def _collect_tool_names(messages: list[ModelRequest | ModelResponse]) -> set[str]:
    """All tool_names the agent invoked during this run.

    We discriminate by `tool_name` rather than `tool_call_id` because LLMs
    reliably echo back the semantic tool name, but not the opaque
    provider-generated call ID (e.g. `toolu_vrtx_018Mgk8rcfAiyZrB46vTzYKa`).
    Forcing exact-id matching produces correct rejections of fabricated calls
    but also rejects faithful citations — net negative for the contract.
    """
    names: set[str] = set()
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    names.add(part.tool_name)
    return names


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

        def _check(label: str, cites: list[ToolCitation]) -> None:
            for c in cites:
                if c.tool_name not in used_tools:
                    raise ModelRetry(
                        f"{label} cites tool_name={c.tool_name!r}, which you did NOT call "
                        f"this turn. Tools you actually called: {sorted(used_tools)}. "
                        f"Either cite one of those or remove the field."
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
        )
        return output
