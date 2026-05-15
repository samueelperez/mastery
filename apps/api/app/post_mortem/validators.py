"""Validators para el output del `post_mortem_agent`.

Mismo contrato base que el main + review validators (tool_name discriminator
para citations), más reglas específicas de PostMortem:

1. ≥1 citation, todas con `tool_name` ∈ tools llamadas este turn.
2. NUNCA citar `get_multi_tf_confluence` — el post-mortem está auditando
   ese scorer, no puede usarlo como evidencia (sería circular).
3. `failure_factors` y `success_factors` deben ser strings con shape válido
   (`name@tf` o tag simple). El check exacto contra factor_snapshot vive en
   el dispatcher (que tiene la fuente de verdad).
4. Length caps explícitos (mejor mensaje de retry que ValidationError).
"""

from __future__ import annotations

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart

from app.agent.deps import AgentDeps
from app.agent.models import PostMortem


from app.agent._validator_utils import collect_tool_names as _collect_tool_names


# Tools que el post-mortem NO puede citar como evidencia. `get_multi_tf_
# confluence` está siendo auditado — citarlo es circular. `log_trade` y
# `create_alert` son mutadores, no fuentes de evidencia.
_BANNED_CITATION_TOOLS: frozenset[str] = frozenset({
    "get_multi_tf_confluence",
    "log_trade",
    "create_alert",
    "delete_alert",
})


def _is_valid_factor_key(key: str) -> bool:
    """Acepta tanto `name@tf` (deterministic) como `name` solo (semantic).
    NO valida contra el snapshot real — el dispatcher hace ese check con la
    fuente de verdad."""
    if not key or not isinstance(key, str):
        return False
    if "@" in key:
        name, tf = key.split("@", 1)
        return bool(name and tf in {"15m", "1h", "4h", "1d"})
    # Semantic tag: solo letras minúsculas, dígitos, underscores.
    return all(c.isalnum() or c == "_" for c in key) and key.islower()


def register_post_mortem_validators(agent: Agent[AgentDeps, PostMortem]) -> None:
    @agent.output_validator
    async def enforce_post_mortem_contract(
        ctx: RunContext[AgentDeps],
        output: PostMortem,
    ) -> PostMortem:
        # ----------------------------- Citations ----------------------------
        called_tools = _collect_tool_names(list(ctx.messages))
        if not output.citations:
            raise ModelRetry(
                "PostMortem requiere ≥1 citation. Llama una tool relevante "
                "(get_indicators, get_market_structure, get_volume_profile, "
                "get_perps_data, get_similar_past_trades, get_factor_hit_rates) "
                "y cita la cifra concreta que respalda tu veredicto."
            )
        for i, c in enumerate(output.citations):
            if not c.tool_name:
                raise ModelRetry(
                    f"`citations[{i}].tool_name` está vacío. Debe ser el "
                    f"nombre literal de la tool que llamaste este turn."
                )
            if c.tool_name in _BANNED_CITATION_TOOLS:
                raise ModelRetry(
                    f"`citations[{i}].tool_name='{c.tool_name}'` está prohibida "
                    f"en post-mortem (auditoría circular o tool mutadora). "
                    f"Usa get_indicators / get_market_structure / "
                    f"get_volume_profile / get_perps_data en su lugar."
                )
            if c.tool_name not in called_tools:
                ctx.deps.log.debug(
                    "post_mortem.citation_invalid_tool",
                    cited=c.tool_name,
                    called=sorted(called_tools),
                )
                raise ModelRetry(
                    f"`citations[{i}].tool_name='{c.tool_name}'` no fue "
                    f"llamada este turn. Tools llamadas: "
                    f"{sorted(called_tools) or '(ninguna)'}. Llama la tool y "
                    f"luego cítala, o elimina la citation."
                )

        # ------------------------ Factor key shape --------------------------
        invalid_failure = [k for k in output.failure_factors if not _is_valid_factor_key(k)]
        invalid_success = [k for k in output.success_factors if not _is_valid_factor_key(k)]
        if invalid_failure or invalid_success:
            bad = invalid_failure + invalid_success
            raise ModelRetry(
                f"factor keys con shape inválido: {bad}. Formato deterministic: "
                f"'name@tf' (tf ∈ 15m/1h/4h/1d, ej. 'ema_stack@1h'). Formato "
                f"semantic: solo el tag en lowercase (ej. 'lvn_support'). "
                f"Copia las claves EXACTAS del factor_snapshot que recibiste."
            )

        # --------------------- Verdict consistency soft --------------------
        # thesis_held + ningún success_factor: contradicción soft. Si la
        # tesis aguantó, debería haber al menos un factor que aportó.
        if output.verdict == "thesis_held" and not output.success_factors:
            raise ModelRetry(
                "verdict='thesis_held' pero `success_factors` está vacía. "
                "Si la tesis se cumplió, identifica AL MENOS un factor del "
                "snapshot que aportó (ej. 'ema_stack@1h', 'lvn_support')."
            )
        # thesis_broken + ningún failure_factor: idem inverso.
        if output.verdict == "thesis_broken" and not output.failure_factors:
            raise ModelRetry(
                "verdict='thesis_broken' pero `failure_factors` está vacía. "
                "Si la tesis se rompió, identifica AL MENOS un factor del "
                "snapshot que falló (ej. 'rsi@1h', 'fvg_fill')."
            )
        # noise: ambas listas deben estar vacías o casi (atribuir factores
        # cuando es ruido es exactamente lo que queremos evitar).
        if output.verdict == "noise" and (output.failure_factors or output.success_factors):
            raise ModelRetry(
                "verdict='noise' implica resultado dominado por wicks/whipsaw "
                "— NO atribuyas a factores. Vacía `failure_factors` y "
                "`success_factors` (o cambia el verdict si crees que algún "
                "factor realmente contribuyó)."
            )

        # ------------------------- Length caps -----------------------------
        if len(output.lesson_es) < 40:
            raise ModelRetry(
                f"`lesson_es` tiene {len(output.lesson_es)} chars — mínimo 40. "
                f"Una lección extrapolable, no un fragmento."
            )
        if len(output.lesson_es) > 400:
            raise ModelRetry(
                f"`lesson_es` tiene {len(output.lesson_es)} chars — máximo "
                f"400. Una frase accionable, no un párrafo."
            )
        if output.counterfactual_es is not None and len(output.counterfactual_es) > 400:
            raise ModelRetry(
                f"`counterfactual_es` tiene {len(output.counterfactual_es)} "
                f"chars — máximo 400. Una frase tipo 'si hubieras X...'."
            )

        ctx.deps.log.info(
            "post_mortem.output_validated",
            verdict=output.verdict,
            calibration=output.confidence_calibration,
            n_failure_factors=len(output.failure_factors),
            n_success_factors=len(output.success_factors),
            has_counterfactual=output.counterfactual_es is not None,
        )

        return output
