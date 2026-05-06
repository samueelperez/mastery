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

import re
from typing import Any

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)

from app.agent.deps import AgentDeps
from app.agent.models import BiasAlert, BriefAnalysis, ToolCitation, TradeIdea
from app.storage.setup_repo import insert_setup_from_idea


# Pattern for tool-name leakage in user-facing prose. Cualquier referencia a
# `get_*` (nombres de las tools registradas) en verdict/catalyst/risk de un
# BriefAnalysis es plumbing visible — el usuario no debe ver nombres de
# funciones, solo conceptos.
_TOOL_LEAK_PATTERN = re.compile(r"\bget_[a-z_]+\b")

# Verdict que propone esperar / no entrar / vigilar. Cuando un BriefAnalysis
# emite confidence='high' con un verdict de pausa, hay incoherencia: la
# confianza alta debe respaldar acción inmediata, no el "espera". Soft-degrade
# a medium en lugar de rebotar (ModelRetry) porque es un ajuste fino — el
# análisis sigue siendo válido, solo recalibramos la etiqueta.
_VERDICT_PAUSE_PATTERN = re.compile(
    r"\b(?:espera(?:r|ndo)?|aún no|no entres|pullback necesario|"
    r"vigilar|esperar a|no compres|no vendas)\b",
    re.IGNORECASE,
)

# Sentence terminator followed by whitespace or end-of-string. Evita contar
# decimales (78.0, +2.1) y miles con punto (82.850). Solo cuenta puntos que
# realmente cierran una frase.
_SENTENCE_END = re.compile(r"[.!?]+(?:\s|$)")


def _count_sentences(text: str) -> int:
    """Count sentence-ending punctuation (`.`, `!`, `?`) that's followed by
    whitespace or end of string. Handles ellipses (counted as 1)."""
    return len(_SENTENCE_END.findall(text))


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


# Tipos de bias que activan el gate "no operes" cuando severidad es high.
# Excluye disposition_effect (es patrón de exit, no señal de "no entres ahora")
# y FOMO (heurística depende de tags del usuario, demasiado frágil para gate
# automático — el agente lo puede mencionar en risk_notes pero no bloquea).
_BIAS_KINDS_THAT_GATE: frozenset[str] = frozenset(
    {"revenge_trading", "overtrading", "oversize_position"}
)


def _collect_stale_tool_warnings(
    messages: list[ModelRequest | ModelResponse],
) -> list[tuple[str, str]]:
    """Recoge `(tool_name, warning)` de cualquier provenance.warnings que
    arranque con 'stale:'. Esto indica que la data subyacente al análisis
    es vieja respecto al timeframe — el agente NO debe vender confianza
    alta sobre data desfasada.
    """
    out: list[tuple[str, str]] = []
    for msg in messages:
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if not isinstance(part, ToolReturnPart):
                continue
            try:
                payload = part.model_response_object()
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            prov = payload.get("provenance")
            if not isinstance(prov, dict):
                continue
            warnings = prov.get("warnings")
            if not isinstance(warnings, list):
                continue
            for w in warnings:
                if isinstance(w, str) and w.startswith("stale:"):
                    out.append((part.tool_name, w))
    return out


def _extract_aggregate_bias(
    messages: list[ModelRequest | ModelResponse],
) -> str | None:
    """Recoge el `aggregate_bias` del último output de
    `get_multi_tf_confluence` en este turn. None si la tool no se llamó o
    si el shape del output no contiene el campo. Lo usa el gate side↔bias
    que evita "forced longs" cuando el agregado contradice la dirección
    pedida por el user.
    """
    for msg in messages:
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if not isinstance(part, ToolReturnPart):
                continue
            if part.tool_name != "get_multi_tf_confluence":
                continue
            try:
                payload = part.model_response_object()
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            data = payload.get("data")
            if isinstance(data, dict):
                bias = data.get("aggregate_bias")
                if isinstance(bias, str):
                    return bias
    return None


def _collect_lvn_zones(
    messages: list[ModelRequest | ModelResponse],
) -> list[tuple[float, float, float]]:
    """Lee los `low_volume_nodes` del último output de `get_volume_profile`
    y devuelve `[(low_bound, high_bound, center_price), ...]` aproximando el
    bin width como `(range_high - range_low) / bins`. Lista vacía si la tool
    no se llamó o si el shape del output no contiene los campos.

    Lo usa el gate "ningún target/support cae dentro de un LVN": los LVN son
    zonas de aceleración (vacuum), no soportes/objetivos realistas. Soft-warn
    inicial: solo log + degrade confidence si algún key_level cae en LVN; en
    una segunda iteración (tras observar logs) se promueve a ModelRetry.
    """
    zones: list[tuple[float, float, float]] = []
    # Solo el último output cuenta — si la tool se llamó múltiples veces este
    # turn, asumimos que el último es el más relevante para los niveles que
    # el modelo propone ahora.
    latest_payload: dict[str, object] | None = None
    for msg in messages:
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if not isinstance(part, ToolReturnPart):
                continue
            if part.tool_name != "get_volume_profile":
                continue
            try:
                payload = part.model_response_object()
            except Exception:
                continue
            if isinstance(payload, dict):
                latest_payload = payload  # type: ignore[assignment]
    if latest_payload is None:
        return zones
    data = latest_payload.get("data")
    if not isinstance(data, dict):
        return zones
    range_low = data.get("range_low")
    range_high = data.get("range_high")
    bins = data.get("bins")
    lvns = data.get("low_volume_nodes")
    if not (
        isinstance(range_low, (int, float))
        and isinstance(range_high, (int, float))
        and isinstance(bins, int)
        and bins > 0
        and isinstance(lvns, list)
    ):
        return zones
    bin_width = (float(range_high) - float(range_low)) / bins
    if bin_width <= 0:
        return zones
    half = bin_width / 2.0
    for node in lvns:
        if not isinstance(node, dict):
            continue
        price = node.get("price")
        if not isinstance(price, (int, float)):
            continue
        center = float(price)
        zones.append((center - half, center + half, center))
    return zones


def _collect_high_severity_biases(
    messages: list[ModelRequest | ModelResponse],
) -> list[str]:
    """Devuelve los `kind` de bias events con `severity=high` que provienen
    de un `detect_bias_patterns` ejecutado este turno. Lista vacía si no hubo
    flags high o si no se llamó la tool. """
    high_kinds: list[str] = []
    for msg in messages:
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if not isinstance(part, ToolReturnPart):
                continue
            if part.tool_name != "detect_bias_patterns":
                continue
            try:
                payload = part.model_response_object()
            except Exception:
                continue
            # Esperamos {"data": [{kind, severity, ...}, ...], "provenance": {...}}
            if not isinstance(payload, dict):
                continue
            data = payload.get("data")
            if not isinstance(data, list):
                continue
            for flag in data:
                if not isinstance(flag, dict):
                    continue
                kind = flag.get("kind")
                severity = flag.get("severity")
                if (
                    isinstance(kind, str)
                    and isinstance(severity, str)
                    and severity == "high"
                    and kind in _BIAS_KINDS_THAT_GATE
                ):
                    high_kinds.append(kind)
    return high_kinds


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


def register_validators(
    agent: Agent[AgentDeps, BriefAnalysis | TradeIdea | str],
) -> None:
    @agent.output_validator
    async def must_cite_quantitative_claims(
        ctx: RunContext[AgentDeps],
        output: BriefAnalysis | TradeIdea | str,
    ) -> BriefAnalysis | TradeIdea | str:
        # Free-text answers (definitional questions) bypass the citation check.
        if isinstance(output, str):
            return output

        # BriefAnalysis: rama exploratoria con prose curada en 3 campos.
        # No requiere citations (la "evidencia" vive en los tool calls del
        # turno; el modelo la sintetiza) pero sí enforce limpieza:
        # 1) cero tool-name leakage en la prosa que ve el usuario.
        # 2) cero markdown headers/énfasis (la presentación es prose plana).
        # Además, auto-pobla bias_alert (si el usuario pidió revisión y hay
        # flags severity=high) y degrada confidence='low' si la data está
        # stale — misma lógica que TradeIdea.
        if isinstance(output, BriefAnalysis):
            for field_name in ("verdict_es", "catalyst_es", "risk_es"):
                text: str = getattr(output, field_name)
                m = _TOOL_LEAK_PATTERN.search(text)
                if m:
                    ctx.deps.log.debug(
                        "agent.brief_retry",
                        reason="tool_leak",
                        field=field_name,
                        leaked=m.group(),
                    )
                    raise ModelRetry(
                        f"`{field_name}` contiene nombre de tool ({m.group()!r}). "
                        f"Reescribe el campo en lenguaje natural — refiere al "
                        f"CONCEPTO ('la tasa de financiación', 'el interés "
                        f"abierto') no al nombre de la función."
                    )
                if "##" in text or "**" in text:
                    ctx.deps.log.debug(
                        "agent.brief_retry",
                        reason="markdown",
                        field=field_name,
                    )
                    raise ModelRetry(
                        f"`{field_name}` contiene markdown (## o **). El "
                        f"frontend renderiza estos campos como prosa plana — "
                        f"sin headers, sin negritas. Reescribe sin markdown."
                    )

            # Sentence count caps — endurece el "1-2 frases" / "MAX 3 frases"
            # que el prompt pide pero el modelo tiende a ignorar.
            verdict_sentences = _count_sentences(output.verdict_es)
            if verdict_sentences > 2:
                ctx.deps.log.debug(
                    "agent.brief_retry",
                    reason="verdict_too_many_sentences",
                    count=verdict_sentences,
                    cap=2,
                )
                raise ModelRetry(
                    f"`verdict_es` tiene {verdict_sentences} frases — máximo "
                    f"2. El veredicto es 'qué hacer YA', no debe contener "
                    f"justificación (eso va en catalyst_es). Comprime a 1-2 "
                    f"frases punchy."
                )
            catalyst_sentences = _count_sentences(output.catalyst_es)
            if catalyst_sentences > 3:
                ctx.deps.log.debug(
                    "agent.brief_retry",
                    reason="catalyst_too_many_sentences",
                    count=catalyst_sentences,
                    cap=3,
                )
                raise ModelRetry(
                    f"`catalyst_es` tiene {catalyst_sentences} frases — máximo "
                    f"3. Comprime: una fuente por frase, una cifra por fuente. "
                    f"Si tienes 5 cifras, elige las 3 que más mueven la tesis. "
                    f"Estructura+momentum cuentan como UNA fuente; el conflicto "
                    f"va en una frase, no en dos."
                )

            stale_warnings = _collect_stale_tool_warnings(list(ctx.messages))
            if stale_warnings and output.confidence != "low":
                output.confidence = "low"

            # Validator A — confidence-vs-verdict alignment (soft-degrade).
            # Si verdict_es propone esperar pero confidence='high', es
            # incoherencia: la confianza alta debe respaldar acción inmediata.
            # Bajamos a 'medium' silenciosamente y logueamos para auditar.
            # No usamos ModelRetry porque el análisis es válido, solo la
            # etiqueta estaba miscalibrada — recalibrarla evita retry caros.
            if (
                output.confidence == "high"
                and _VERDICT_PAUSE_PATTERN.search(output.verdict_es)
            ):
                ctx.deps.log.info(
                    "validator.confidence_pause_mismatch",
                    verdict=output.verdict_es[:80],
                    original_confidence="high",
                    new_confidence="medium",
                )
                output.confidence = "medium"

            # Validator B — LVN check sobre key_levels (FASE 1: soft-warn).
            # Si algún key_level con kind ∈ {target, support, resistance} cae
            # dentro del rango de un LVN (zona de aceleración / vacuum),
            # logueamos el caso y degradamos confidence si era 'high'.
            # Los LVN no son soporte ni objetivo realista — el precio acelera
            # al cruzarlos. En una segunda iteración (tras 1-2 semanas
            # revisando logs) promovemos a ModelRetry.
            lvn_zones = _collect_lvn_zones(list(ctx.messages))
            if lvn_zones:
                offending: list[tuple[str, float, float]] = []
                for kl in output.key_levels:
                    if kl.kind not in ("target", "support", "resistance"):
                        continue
                    for low, high, center in lvn_zones:
                        if low <= kl.price <= high:
                            offending.append((kl.label, kl.price, center))
                            break
                if offending:
                    ctx.deps.log.info(
                        "validator.level_in_lvn",
                        offending=[
                            {"label": lbl, "price": p, "lvn_center": c}
                            for lbl, p, c in offending
                        ],
                        n_offending=len(offending),
                    )
                    if output.confidence == "high":
                        output.confidence = "medium"

            high_biases = _collect_high_severity_biases(list(ctx.messages))
            if high_biases:
                kinds = sorted(set(high_biases))
                output.bias_alert = BiasAlert(
                    kinds=kinds,
                    severity="high",
                    message=(
                        f"Tu journal muestra {' + '.join(kinds)} con severidad "
                        f"alta en las últimas horas. Considera procesar antes "
                        f"de operar — el setup técnico puede ser válido pero "
                        f"la ejecución suele degradarse en este estado."
                    ),
                )

            # Drift metrics: lengths + sentence counts. Útil para ver si el
            # modelo se acerca a los caps (señal de que el prompt necesita
            # endurecerse antes de que rebote en producción).
            ctx.deps.log.info(
                "agent.brief_validated",
                symbol=output.symbol,
                timeframe=output.timeframe,
                confidence=output.confidence,
                n_levels=len(output.key_levels),
                verdict_len=len(output.verdict_es),
                verdict_sentences=verdict_sentences,
                catalyst_len=len(output.catalyst_es),
                catalyst_sentences=catalyst_sentences,
                risk_len=len(output.risk_es),
                bias_alert=output.bias_alert is not None,
            )
            return output

        used_tools = _collect_tool_names(list(ctx.messages))
        returned_handles = _collect_returned_handles(list(ctx.messages))

        # Stale-data gate: si CUALQUIER tool emitió un warning 'stale:' en su
        # provenance, el TradeIdea no puede vender confidence != 'low'. La
        # data subyacente está desfasada respecto al timeframe — la honesty
        # gana a la confianza vendida. El agente sigue puede proponer la
        # idea, sólo baja la confianza para que el usuario lo lea con la
        # cautela debida.
        stale_warnings = _collect_stale_tool_warnings(list(ctx.messages))
        if stale_warnings and output.confidence != "low":
            preview = "; ".join(f"{name}: {w}" for name, w in stale_warnings[:3])
            raise ModelRetry(
                f"Confidence gate (stale data): {len(stale_warnings)} tool(s) "
                f"emitieron warnings 'stale:' este turno ({preview}). Cuando "
                f"la data está desfasada respecto al timeframe, `confidence` "
                f"DEBE ser 'low'. Cambia confidence='low' (puedes mantener la "
                f"idea pero el usuario debe saber que la data subyacente no "
                f"está fresca)."
            )

        # Bias auto-surface (NO gate): si detect_bias_patterns devolvió flags
        # `severity=high` de tipos críticos, los exponemos como `bias_alert`
        # para que la UI los pinte como banner separado. La decisión de
        # operar es del usuario; nuestro trabajo es analizar y avisar, no
        # bloquear. Antes había un gate hard que forzaba no_trade — paterna-
        # lismo que enterraba el análisis. Esta versión respeta al usuario.
        high_biases = _collect_high_severity_biases(list(ctx.messages))
        if high_biases:
            kinds = sorted(set(high_biases))
            output.bias_alert = BiasAlert(
                kinds=kinds,
                severity="high",
                message=(
                    f"Tu journal muestra {' + '.join(kinds)} con severidad "
                    f"alta en las últimas horas. Considera procesar antes "
                    f"de operar — el setup técnico puede ser válido pero "
                    f"la ejecución suele degradarse en este estado."
                ),
            )

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

        # Position sizing — requerido para non-no_trade ideas.
        # El system prompt instruye al modelo a calcular position_size_pct +
        # leverage_x desde trader_profile.risk_per_trade_pct. Si los omite,
        # el copilot no puede dimensionar y el usuario va a opera "a ojo".
        if (
            output.direction in ("long", "short")
            and output.entry is not None
            and output.invalidation is not None
        ):
            if output.position_size_pct is None:
                raise ModelRetry(
                    f"direction='{output.direction}' requiere `position_size_pct`. "
                    f"Calcula: stop_distance_pct = |entry − invalidation| / entry, "
                    f"position_size_pct = risk_per_trade_pct / stop_distance_pct. "
                    f"Ver sección 'Position sizing' del system prompt."
                )
            if output.leverage_x is None:
                raise ModelRetry(
                    f"direction='{output.direction}' requiere `leverage_x`. "
                    f"Si position_size_pct ≤ 100, leverage_x=1. Si excede, "
                    f"leverage_x = ceil(position_size_pct/100), capeado a "
                    f"trader_profile.max_leverage."
                )
            # Coherencia matemática: posición × leverage debe coincidir
            # aproximadamente con el riesgo declarado del perfil.
            stop_distance_pct = (
                abs(output.entry - output.invalidation) / output.entry * 100
            )
            if stop_distance_pct > 0:
                # risk_per_trade_pct implícito = position_size_pct × stop_distance_pct / 100
                # No imponemos un floor exacto; sólo que no sea absurdo (>10%
                # del capital de riesgo en un solo trade).
                implied_risk_pct = (
                    output.position_size_pct
                    * output.leverage_x
                    * stop_distance_pct
                    / 100
                )
                if implied_risk_pct > 10.0:
                    raise ModelRetry(
                        f"position_size_pct={output.position_size_pct:.1f}% × "
                        f"leverage={output.leverage_x}× × stop_distance="
                        f"{stop_distance_pct:.2f}% implica arriesgar "
                        f"{implied_risk_pct:.1f}% del capital en este trade. "
                        f"Eso es demasiado para single-trade risk. Reduce "
                        f"position_size_pct o aleja el SL."
                    )

        # Side ↔ aggregate_bias coherence gate.
        # Defensa contra "forced longs" cuando el user pide direccional pero
        # el análisis multi-TF no lo respalda. Setups contra-tendencia
        # (mean-reversion, oversold bounces) son válidos PERO obligamos
        # `confidence='low'` para que el modelo reconozca el conflicto en
        # summary_es y la UI lo pinte con el chip amber visible. Si el
        # modelo emitió confidence='medium' o 'high' contra el agregado,
        # rebotamos y el modelo decide: bajar confianza o no_trade.
        if output.direction in ("long", "short"):
            aggregate_bias = _extract_aggregate_bias(list(ctx.messages))
            if aggregate_bias is not None:
                contradicts = (
                    (output.direction == "long" and aggregate_bias == "bear")
                    or (output.direction == "short" and aggregate_bias == "bull")
                )
                if contradicts and output.confidence != "low":
                    raise ModelRetry(
                        f"direction='{output.direction}' contradice "
                        f"aggregate_bias='{aggregate_bias}' del multi-TF "
                        f"confluence con confidence='{output.confidence}'. "
                        f"Setups contra-tendencia son válidos PERO exigen "
                        f"confidence='low' Y que summary_es reconozca el "
                        f"conflicto explícitamente. Opciones: "
                        f"(a) downgrade a confidence='low' y añade en "
                        f"summary_es 'aunque el agregado es {aggregate_bias}, "
                        f"hay setup contra-tendencia válido porque <razón "
                        f"concreta>'; "
                        f"(b) cambia direction='no_trade' y explica qué "
                        f"falta y qué triggers reabrirían la operativa."
                    )

        # Risk:Reward sanity check.
        # Un setup con R:R < 1 al primer TP tiene esperanza matemática negativa
        # incluso con winrate 50%. Lo bloqueamos: o el LLM ajusta entry/SL/TPs,
        # o cambia direction a "no_trade".
        if (
            output.direction in ("long", "short")
            and output.entry is not None
            and output.invalidation is not None
            and output.targets
        ):
            risk = abs(output.entry - output.invalidation)
            if risk == 0:
                raise ModelRetry(
                    f"entry={output.entry} == invalidation={output.invalidation} → "
                    f"riesgo nulo, R:R indefinido. Pon un SL lógico (no en el entry) "
                    f"o cambia direction='no_trade'."
                )
            first_tp = output.targets[0]
            if output.direction == "long":
                reward = first_tp.price - output.entry
                # Sanity check: TP debe estar POR ENCIMA del entry en long
                if reward <= 0:
                    raise ModelRetry(
                        f"direction='long' pero {first_tp.label}={first_tp.price} ≤ "
                        f"entry={output.entry}. En long el TP va POR ENCIMA del entry. "
                        f"Revisa los niveles o cambia direction='short'/'no_trade'."
                    )
                # Y el SL POR DEBAJO
                if output.invalidation >= output.entry:
                    raise ModelRetry(
                        f"direction='long' pero invalidation={output.invalidation} ≥ "
                        f"entry={output.entry}. En long el SL va POR DEBAJO del entry."
                    )
            else:  # short
                reward = output.entry - first_tp.price
                if reward <= 0:
                    raise ModelRetry(
                        f"direction='short' pero {first_tp.label}={first_tp.price} ≥ "
                        f"entry={output.entry}. En short el TP va POR DEBAJO del entry."
                    )
                if output.invalidation <= output.entry:
                    raise ModelRetry(
                        f"direction='short' pero invalidation={output.invalidation} ≤ "
                        f"entry={output.entry}. En short el SL va POR ENCIMA del entry."
                    )
            rr = reward / risk
            if rr < 1.5:
                raise ModelRetry(
                    f"R:R al primer TP es {rr:.2f}:1 (reward={reward:.2f} / "
                    f"risk={risk:.2f}). Mínimo aceptable 1.5:1 — si el setup no "
                    f"lo permite, mueve el SL más cerca del entry, ajusta el TP a "
                    f"un nivel más lejano (cita tool_name=get_market_structure), "
                    f"o cambia direction='no_trade'. NO propongas trades con "
                    f"esperanza negativa."
                )

        # Validator B — LVN check sobre targets (FASE 1: soft-warn).
        # Mismo principio que en BriefAnalysis pero sobre TradeIdea.targets.
        # Un TP dentro de un LVN es objetivo poco realista: el precio acelera
        # al cruzar la zona, no se queda. Soft-warn ahora, hard-reject tras
        # observar logs.
        lvn_zones = _collect_lvn_zones(list(ctx.messages))
        if lvn_zones and output.direction in ("long", "short"):
            offending: list[tuple[str, float, float]] = []
            for tgt in output.targets:
                for low, high, center in lvn_zones:
                    if low <= tgt.price <= high:
                        offending.append((tgt.label, tgt.price, center))
                        break
            if offending:
                ctx.deps.log.info(
                    "validator.target_in_lvn",
                    direction=output.direction,
                    offending=[
                        {"label": lbl, "price": p, "lvn_center": c}
                        for lbl, p, c in offending
                    ],
                    n_offending=len(offending),
                )
                if output.confidence == "high":
                    output.confidence = "medium"

        # Validator A — confidence-vs-summary alignment (soft-degrade).
        # En TradeIdea el "verdict" vive en summary_es (más largo). Si el
        # modelo emite confidence='high' pero el summary recomienda esperar
        # un trigger antes de operar, recalibramos a 'medium'.
        if (
            output.confidence == "high"
            and _VERDICT_PAUSE_PATTERN.search(output.summary_es)
        ):
            ctx.deps.log.info(
                "validator.confidence_pause_mismatch",
                summary=output.summary_es[:120],
                original_confidence="high",
                new_confidence="medium",
            )
            output.confidence = "medium"

        ctx.deps.log.info(
            "agent.output_validated",
            direction=output.direction,
            confidence=output.confidence,
            n_confluences=len(output.confluences),
            n_targets=len(output.targets),
            n_scenarios=len(output.scenarios),
            summary_len=len(output.summary_es),
            handles_returned=len(returned_handles),
            bias_alert=output.bias_alert is not None,
        )

        # Auto-save setup en journal_trades (status='pending') cuando el
        # agente emite un TradeIdea direccional con niveles concretos.
        # Idempotente vía dedup_hash (refinamientos del mismo setup no
        # duplican). Errores de DB se loguean pero no rebotan al usuario —
        # el chat ya generó la respuesta y el journal es side-effect.
        if (
            output.direction in ("long", "short")
            and output.entry is not None
            and output.invalidation is not None
            and output.targets
        ):
            try:
                async with ctx.deps.session_factory() as session:
                    setup_id = await insert_setup_from_idea(
                        session,
                        user_id=ctx.deps.user_id,
                        idea=output,
                    )
                ctx.deps.log.info(
                    "agent.setup_persisted",
                    setup_id=setup_id,
                    deduped=setup_id is None,
                    symbol=output.symbol,
                    timeframe=output.timeframe,
                    side=output.direction,
                )
            except Exception as exc:
                # No bloquear la respuesta del chat por un fallo de journal.
                ctx.deps.log.warning(
                    "agent.setup_persist_failed",
                    error=str(exc),
                    symbol=output.symbol,
                )

        return output
