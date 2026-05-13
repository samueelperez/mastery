"""Validators para el output del `review_agent`.

Mismo contrato base que el main agent's validators (tool_name discriminator
para citations), más reglas específicas de TradeReview:

1. ≥1 citation, todas con `tool_name` ∈ tools llamadas este turn.
2. Length caps duros (summary ≤400, rationale ≤600). Pydantic ya enforce
   max_length pero los hacemos explícitos para mensajes de retry claros.
3. Coherencia state↔recommendation: rebotamos combinaciones imposibles
   (e.g. "reversing" + "hold" silencioso).
"""

from __future__ import annotations

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart

from app.agent.deps import AgentDeps
from app.agent.models import TradeReview


def _collect_tool_names(messages: list[ModelRequest | ModelResponse]) -> set[str]:
    names: set[str] = set()
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    names.add(part.tool_name)
    return names


def register_review_validators(agent: Agent[AgentDeps, TradeReview]) -> None:
    @agent.output_validator
    async def enforce_review_contract(
        ctx: RunContext[AgentDeps],
        output: TradeReview,
    ) -> TradeReview:
        # ----------------------------- Citations ----------------------------
        called_tools = _collect_tool_names(list(ctx.messages))
        if not output.citations:
            raise ModelRetry(
                "TradeReview requiere ≥1 citation. Llama una tool relevante "
                "(get_indicators, get_market_structure, get_volume_profile, "
                "get_multi_tf_confluence, get_perps_data, etc.) y cita "
                "concretamente la cifra que la respalda."
            )
        for i, c in enumerate(output.citations):
            if not c.tool_name:
                raise ModelRetry(
                    f"`citations[{i}].tool_name` está vacío. Debe ser el "
                    f"nombre literal de la tool que llamaste este turn."
                )
            if c.tool_name not in called_tools:
                ctx.deps.log.debug(
                    "review.citation_invalid_tool",
                    cited=c.tool_name,
                    called=sorted(called_tools),
                )
                raise ModelRetry(
                    f"`citations[{i}].tool_name='{c.tool_name}'` no fue "
                    f"llamada este turn. Tools llamadas: "
                    f"{sorted(called_tools) or '(ninguna)'}. Llama la tool "
                    f"y luego cítala, o elimina la citation."
                )

        # ----------------------------- Coherencia ---------------------------
        # `reversing` + `hold` es contradicción de tesis: la review dice que
        # la tesis se está rompiendo pero recomienda no hacer nada. O bien
        # el state debería ser `at_risk`, o bien la recomendación debería
        # ser partial_close/exit_now.
        if output.current_state == "reversing" and output.recommendation == "hold":
            raise ModelRetry(
                "Incoherencia: current_state='reversing' pero recommendation="
                "'hold'. Si la tesis se está rompiendo, no se sostiene en "
                "silencio. Sube a 'partial_close' o 'exit_now' (preferir "
                "partial_close ante duda) — o reclasifica state a 'at_risk' "
                "si el deterioro aún no rompe estructura."
            )

        # `on_track` + `exit_now` es over-reactivo: si todo va bien, no salimos.
        # Si la review detecta razones reales para salir, el state debería ser
        # at_risk o reversing.
        if output.current_state == "on_track" and output.recommendation == "exit_now":
            raise ModelRetry(
                "Incoherencia: current_state='on_track' pero recommendation="
                "'exit_now'. Si la tesis se mantiene, no se cierra el trade "
                "precipitadamente. Reclasifica el state si ves razones para "
                "salir, o cambia la recomendación a 'tighten_sl' / 'hold'."
            )

        # `on_track` + `partial_close` antes de TP: aceptable si rationale
        # justifica con cifras (R unrealized >1, tiempo en posición, etc.).
        # No rebotamos aquí — confiamos en que el rationale lo explique.

        # ------------------------- Length caps explícitos --------------------
        # Pydantic ya enforce max_length pero un retry con mensaje claro
        # es más útil que un ValidationError genérico.
        if len(output.summary) > 400:
            raise ModelRetry(
                f"`summary` tiene {len(output.summary)} chars — máximo 400. "
                f"Comprime a 2-3 frases dense de trader."
            )
        if len(output.rationale) > 600:
            raise ModelRetry(
                f"`rationale` tiene {len(output.rationale)} chars — máximo "
                f"600. Cifras concretas, una por frase, sin meta-comentario."
            )

        return output
