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
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolReturnPart,
)

from app.agent.deps import AgentDeps
from app.agent.models import BiasAlert, BriefAnalysis, ToolCitation, TradeIdea
from app.backtest.factor_stats_repo import evaluate_factor_gate
from app.liquidation.factor_snapshot import (
    enrich_with_provider_breakdown as _enrich_heatmap_snapshot,
)
from app.liquidation.factor_snapshot import (
    find_heatmap_citation_snapshot as _find_heatmap_citation_snapshot,
)
from app.setups.events import persist_trade_idea

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


from app.agent._validator_utils import collect_tool_names as _collect_tool_names

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


# Snapshot numeric verification ----------------------------------------------
#
# The citation gate (`_check`) proves the tool was called. The snapshot
# numeric gate proves the cited number came from that tool's actual output.
# Together they prevent both "cited a tool that wasn't called" and "called
# the right tool but quoted an invented number".

# Keys appearing in tool outputs that should NOT be verified numerically:
# - Handle keys (run_id, trade_id) — verified by the handle gate.
# - Configuration / metadata keys — these are inputs the agent passed or
#   structural metadata, not "facts" the agent should be quoting.
_NON_NUMERIC_SNAPSHOT_KEYS: frozenset[str] = frozenset(
    {
        *_HANDLE_KEYS,
        *_HANDLE_LIST_KEYS,
        "symbol",
        "exchange",
        "source",
        "as_of",
        "timeframe",
        "tf",
        "bins",
        "lookback",
        "lookback_bars",
        "length",
        "pivot_strength_used",
    }
)


def _collect_tool_outputs(
    messages: list[ModelRequest | ModelResponse],
) -> dict[str, list[dict[str, Any]]]:
    """`tool_name → list of return payloads` observed this turn.

    A tool may be called multiple times in a single turn (e.g. once per TF
    when asking for indicators on 1h, 4h, 1d); each call produces an entry.
    """
    out: dict[str, list[dict[str, Any]]] = {}
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
            if isinstance(payload, dict):
                out.setdefault(part.tool_name, []).append(payload)
    return out


def _find_numeric_values(payload: Any, target_key: str, sink: list[float]) -> None:
    """Recursively collect every numeric scalar stored under ``target_key`` in
    ``payload``. Booleans are excluded (Python's ``bool`` is a subclass of
    ``int``) — they'd otherwise pollute matches with 0.0 / 1.0."""
    if isinstance(payload, dict):
        for k, v in payload.items():
            if k == target_key:
                if isinstance(v, bool):
                    continue
                if isinstance(v, (int, float)):
                    sink.append(float(v))
            else:
                _find_numeric_values(v, target_key, sink)
    elif isinstance(payload, list):
        for item in payload:
            _find_numeric_values(item, target_key, sink)


def _verify_snapshot_numerics(
    citation: ToolCitation,
    tool_outputs: dict[str, list[dict[str, Any]]],
) -> list[tuple[str, float, list[float]]]:
    """Return a list of mismatches detected in this citation's snapshot.

    Each mismatch is a ``(key, cited_value, found_values)`` triple:
    - ``cited_value`` is the numeric value the model placed in the snapshot.
    - ``found_values`` are all numeric values stored under that key in any of
      the tool's outputs this turn.
    - None of the ``found_values`` are within tolerance of ``cited_value``.

    Tolerance: 0.1% relative (max with 1e-6 absolute for near-zero values).
    Empty list ⇒ every checkable key matches some output. A key that doesn't
    appear in any output is silently skipped (the cited value may be derived
    from the output, not present verbatim — e.g. midpoint of two prices).
    """
    mismatches: list[tuple[str, float, list[float]]] = []
    payloads = tool_outputs.get(citation.tool_name, [])
    if not payloads:
        return mismatches  # the citation gate handles tool-name correlation

    for key, cited in (citation.snapshot or {}).items():
        if key in _NON_NUMERIC_SNAPSHOT_KEYS:
            continue
        if isinstance(cited, bool):
            continue
        if not isinstance(cited, (int, float)):
            continue
        cited_f = float(cited)

        found: list[float] = []
        for payload in payloads:
            _find_numeric_values(payload, key, found)
        if not found:
            continue  # key absent in output → unverifiable, soft-pass

        tol_rel = 1e-3
        tol_abs = 1e-6
        match = any(abs(cited_f - real) <= max(tol_rel * abs(real), tol_abs) for real in found)
        if not match:
            mismatches.append((key, cited_f, sorted(set(found))))
    return mismatches


def _verify_liquidation_citation(
    label: str,
    cite: Any,
    tool_outputs: dict[str, list[dict[str, Any]]],
    direction: Any,
    confidence: Any,
) -> str | None:
    """Liquidation-specific citation checks beyond `_verify_snapshot_numerics`.

    Returns a short error message describing the problem, or None if the
    citation is well-formed. Caller raises `ModelRetry` with this message.

    Checks:
      - Required keys present in snapshot: symbol, current_price,
        sources_agreement, sources_used.
      - The tool was actually invoked this turn.
      - Some real call returned the cited symbol.
      - current_price within 0.5% of the real value (prices move between
        tool call and citation; loose tolerance).
      - sources_agreement matches real within 0.001 (deterministic per call).
      - For `target {…}` citations only: same-side zone as TP is rejected
        (long setup → TP must be short_liq, not long_liq, and vice versa).
        Same-side zones are valid as INVALIDATION references, hence the
        label gate.
      - confidence='high' is incompatible with sources_agreement < 0.60.
    """
    snap = cite.snapshot or {}

    required = {"symbol", "current_price", "sources_agreement", "sources_used"}
    missing = required - snap.keys()
    if missing:
        return f"missing required snapshot keys {sorted(missing)}. Re-cite with the full snapshot."

    payloads = tool_outputs.get("get_liquidation_heatmap", [])
    if not payloads:
        return (
            "cites get_liquidation_heatmap but the tool was not invoked "
            "this turn. Invoke the tool first, then cite its real output."
        )

    snap_symbol = snap.get("symbol")
    matching: list[dict[str, Any]] = []
    for p in payloads:
        data = p.get("data") if isinstance(p, dict) else None
        if isinstance(data, dict) and data.get("symbol") == snap_symbol:
            matching.append(data)
    if not matching:
        return (
            f"references symbol {snap_symbol!r} but no "
            "get_liquidation_heatmap call this turn returned that symbol."
        )
    real = matching[0]

    try:
        cit_px = float(snap["current_price"])
        real_px = float(real["current_price"])
    except (TypeError, ValueError):
        return "current_price must be numeric."
    if real_px > 0 and abs(cit_px - real_px) / real_px > 0.005:
        return (
            f"current_price in citation ({cit_px}) deviates >0.5% from real "
            f"({real_px}). Re-cite using the most recent tool result."
        )

    try:
        cit_agree = float(snap["sources_agreement"])
        real_agree = float(real["sources_agreement"])
    except (TypeError, ValueError):
        return "sources_agreement must be numeric."
    if abs(cit_agree - real_agree) > 0.001:
        return (
            f"sources_agreement in citation ({cit_agree}) does not match the "
            f"real value ({real_agree})."
        )

    # Direction-vs-zone coherence — only for target citations. Entry/SL/
    # invalidation citations may legitimately reference same-side zones
    # (e.g. SL beyond the nearest same-side cascade).
    if label.startswith("target ") and direction in ("long", "short"):
        if "nearest_short_liq_price" in snap and direction == "short":
            return (
                "TradeIdea direction='short' but target citation references "
                "nearest_short_liq. Short setups TP at long_liq (below current "
                "price), not short_liq."
            )
        if "nearest_long_liq_price" in snap and direction == "long":
            return (
                "TradeIdea direction='long' but target citation references "
                "nearest_long_liq. Long setups TP at short_liq (above current "
                "price), not long_liq."
            )

    if cit_agree < 0.60 and confidence == "high":
        return (
            f"sources_agreement={cit_agree:.3f} is below 0.60 but TradeIdea "
            "claims confidence='high'. Low agreement requires confidence in "
            "{'low','medium'}."
        )

    return None


# Semantic tag verification --------------------------------------------------
#
# Tags in the allowlist (`_ALLOWED_SEMANTIC_TAGS`) pass the spelling check.
# But the agent can still emit ``lvn_support`` without the volume profile
# having actually detected any LVN. This map registers tags whose
# correctness can be checked against the tool output that would have
# produced them. Tags without a registered requirement pass through
# (their semantics are interpretive — we don't fail on those).

_SEMANTIC_TAG_REQUIREMENTS: dict[str, tuple[str, Callable[[dict[str, Any]], bool]]] = {
    "lvn_support": (
        "get_volume_profile",
        lambda data: bool(data.get("low_volume_nodes")),
    ),
    "lvn_resistance": (
        "get_volume_profile",
        lambda data: bool(data.get("low_volume_nodes")),
    ),
    "swept_liquidity": (
        "get_market_structure",
        lambda data: bool(data.get("swing_highs")) or bool(data.get("swing_lows")),
    ),
}


def _verify_semantic_tags(
    tags: list[str],
    tool_outputs: dict[str, list[dict[str, Any]]],
) -> list[str]:
    """Tags lacking supporting structure in this turn's tool outputs.

    A tag is "unsupported" if:
    - It appears in ``_SEMANTIC_TAG_REQUIREMENTS``, AND
    - The required tool was not called this turn OR none of its outputs
      satisfy the structure check.

    Tags outside the registry pass through (interpretive — out of scope for
    automated verification).
    """
    unsupported: list[str] = []
    for tag in tags:
        req = _SEMANTIC_TAG_REQUIREMENTS.get(tag)
        if req is None:
            continue
        tool_name, check = req
        payloads = tool_outputs.get(tool_name, [])
        if not payloads:
            unsupported.append(tag)
            continue
        ok = False
        for payload in payloads:
            data = payload.get("data")
            if isinstance(data, dict) and check(data):
                ok = True
                break
        if not ok:
            unsupported.append(tag)
    return unsupported


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


def _collect_latest_confluence_data(
    messages: list[ModelRequest | ModelResponse],
) -> dict[str, object] | None:
    """Devuelve el `data` (ConfluenceMap shape) del último output de
    `get_multi_tf_confluence` en este turno. None si no se llamó.

    Usado por la persistencia de F5.5: el `factor_snapshot.deterministic`
    se construye a partir de aquí — ScoreComponents por timeframe + agregado.
    """
    latest: dict[str, object] | None = None
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
                latest = data  # type: ignore[assignment]
    return latest


def _confluence_to_deterministic_snapshot(
    cmap: dict[str, object],
) -> dict[str, object]:
    """Convierte el ConfluenceMap.data (JSON shape) al sub-dict
    `factor_snapshot.deterministic`. Mismas claves que el helper público
    en `confluence.py::confluence_map_to_factor_snapshot_deterministic`,
    pero operando sobre el dict ya serializado (no el pydantic model)."""
    by_tf: dict[str, dict[str, float]] = {}
    raw_by_tf = cmap.get("by_tf")
    if isinstance(raw_by_tf, list):
        for entry in raw_by_tf:
            if not isinstance(entry, dict):
                continue
            tf = entry.get("timeframe")
            comp = entry.get("score_components")
            score_total = entry.get("score_total")
            if not isinstance(tf, str) or not isinstance(comp, dict):
                continue
            row: dict[str, float] = {}
            for key in ("ema_stack", "regime", "rsi", "volume", "distance_atr"):
                v = comp.get(key)
                if isinstance(v, (int, float)):
                    row[key] = float(v)
            if isinstance(score_total, (int, float)):
                row["score_total"] = float(score_total)
            by_tf[tf] = row
    return {
        "by_tf": by_tf,
        "aggregate_bias": cmap.get("aggregate_bias"),
        "aggregate_agreement_pct": cmap.get("aggregate_agreement_pct"),
    }


# Vocabulario controlado de semantic_tags. El agente puede emitir tags fuera
# de esta lista en TradeIdea.semantic_tags pero el validator los descarta
# antes de persistir — mantiene `factor_outcomes.factor_name` predecible
# para queries de agregación.
_ALLOWED_SEMANTIC_TAGS: frozenset[str] = frozenset(
    {
        "lvn_support",
        "lvn_resistance",
        "fvg_fill",
        "fvg_imbalance",
        "vwap_reclaim",
        "vwap_rejection",
        "swept_liquidity",
        "btc_correlation_breakdown",
        "funding_squeeze",
        "oi_divergence",
        "session_open_us",
        "session_open_asia",
        "session_open_eu",
        "weekend_low_liquidity",
        "post_news_breakout",
        "mean_reversion_setup",
        "trend_continuation_setup",
    }
)


def _filter_semantic_tags(tags: list[str]) -> list[str]:
    """Mantiene solo los tags en el vocabulario controlado, deduplicados
    preservando orden de inserción."""
    seen: set[str] = set()
    out: list[str] = []
    for tag in tags:
        if not isinstance(tag, str):
            continue
        norm = tag.strip().lower()
        if norm in _ALLOWED_SEMANTIC_TAGS and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


def _build_factor_snapshot(
    *,
    messages: list[ModelRequest | ModelResponse],
    output: object,
) -> dict[str, object] | None:
    """Construye el dict `factor_snapshot` que se persistirá en
    `journal_trades.factor_snapshot`. Devuelve None si ningún cerebro citado
    este turno produjo data persistible (ni `get_multi_tf_confluence` ni
    `get_liquidation_heatmap`).

    Shape (version=1):
        {
          "version": 1,
          "captured_at": ISO ts,
          "deterministic"?: {
            "by_tf": {"1h": {"ema_stack": .., ...}, "4h": {...}, ...},
            "aggregate_bias": "bull"|"bear"|"range",
            "aggregate_agreement_pct": float
          },                                       # presente si confluence se llamó
          "semantic_tags": [...] (allow-list filtered),
          "context": {"regime_label": "...", "entry_tf": "..."},
          "get_liquidation_heatmap"?: {
            "symbol": ...,
            "current_price": ...,
            "sources_agreement": ...,
            "sources_used": [...],
            "nearest_long_liq_price"? | "nearest_short_liq_price"?,
            "timeframe"?, "source_breakdown_a_price"?,
            "source_breakdown_b_price"?
          }                                        # presente si Cerebro 1 fue citado
        }
    """
    cdata = _collect_latest_confluence_data(messages)

    # Cerebro 1: si la TradeIdea cita `get_liquidation_heatmap`, persistimos
    # la snapshot enriquecida con per-provider price breakdown. Sin esto el
    # handler de Telegram `record_ground_truth` loguea
    # `gt_no_heatmap_citation` y la calibración M2 llega sin signal.
    heatmap_snap = _find_heatmap_citation_snapshot(output)
    if heatmap_snap is not None:
        heatmap_snap = _enrich_heatmap_snapshot(heatmap_snap, messages)

    if cdata is None and heatmap_snap is None:
        return None

    # semantic_tags solo existen en TradeIdea (no en BriefAnalysis). Acceso
    # defensivo por si el caller pasa otro modelo.
    raw_tags = getattr(output, "semantic_tags", None) or []
    semantic_tags = _filter_semantic_tags(list(raw_tags))

    # Contexto: régimen + timeframe de entrada. Otros campos (atr_pct_1h,
    # session, etc.) los puede añadir el dispatcher de post-mortem desde
    # OHLCV reciente — el snapshot inicial los deja fuera para no inflar.
    regime_label = getattr(getattr(output, "regime", None), "label", None)
    entry_tf = getattr(output, "timeframe", None)

    out: dict[str, object] = {
        "version": 1,
        "captured_at": datetime.now(tz=UTC).isoformat(),
        "semantic_tags": semantic_tags,
        "context": {
            "regime_label": regime_label,
            "entry_tf": entry_tf,
        },
    }
    if cdata is not None:
        out["deterministic"] = _confluence_to_deterministic_snapshot(cdata)
    if heatmap_snap is not None:
        out["get_liquidation_heatmap"] = heatmap_snap

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
    flags high o si no se llamó la tool."""
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
            if output.confidence == "high" and _VERDICT_PAUSE_PATTERN.search(output.verdict_es):
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
                            {"label": lbl, "price": p, "lvn_center": c} for lbl, p, c in offending
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
            ("stop_loss", output.stop_loss, output.stop_loss_citations),
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

        # Pre-entry invalidation conditions: each must cite a real tool and
        # be anchored to a sensible (symbol, timeframe). Cross-symbol is
        # allowed ONLY when symbol == BTCUSDT, idea symbol is a non-BTC
        # altcoin, AND the condition cites get_btc_correlation — the
        # correlation gate prevents "throw BTC at every altcoin".
        idea_symbol_upper = output.symbol.upper()
        for i, cond in enumerate(output.invalidation_conditions):
            label = f"invalidation_condition[{i}]"
            if not cond.citations:
                raise ModelRetry(
                    f"{label} requires at least one ToolCitation "
                    f"(tool_name from {sorted(used_tools)})."
                )
            _check(label, cond.citations)
            cond_symbol = cond.spec.symbol.upper()
            if cond_symbol != idea_symbol_upper:
                is_alt_with_btc_anchor = cond_symbol == "BTCUSDT" and idea_symbol_upper != "BTCUSDT"
                if not is_alt_with_btc_anchor:
                    raise ModelRetry(
                        f"{label} references symbol={cond_symbol!r} but the "
                        f"idea is on {idea_symbol_upper!r}. Cross-symbol "
                        f"invalidation only allowed when the idea is on a "
                        f"non-BTC altcoin AND the condition spec is on "
                        f"BTCUSDT AND its citations include "
                        f"get_btc_correlation."
                    )
                cites_btc_corr = any(c.tool_name == "get_btc_correlation" for c in cond.citations)
                if not cites_btc_corr:
                    raise ModelRetry(
                        f"{label} is cross-symbol (BTCUSDT vs idea "
                        f"{idea_symbol_upper!r}) but does NOT cite "
                        f"get_btc_correlation. Add a citation referencing "
                        f"that tool's output showing high pearson, or move "
                        f"the condition back to symbol={idea_symbol_upper!r}."
                    )

        # `expires_at` is opt-in. When the agent emits it, it's an
        # auditable claim — must be tz-aware UTC, strictly in the future,
        # and accompanied by rationale + ≥1 citation.
        if output.expires_at is not None:
            if output.expires_at.tzinfo is None:
                raise ModelRetry(
                    "`expires_at` must be timezone-aware UTC (e.g. "
                    "'2026-05-12T14:30:00Z'). Got a naive datetime."
                )
            now_utc = datetime.now(tz=UTC)
            if output.expires_at <= now_utc:
                raise ModelRetry(
                    f"`expires_at={output.expires_at.isoformat()}` is not "
                    f"in the future (now={now_utc.isoformat()}). Either "
                    f"emit a future timestamp or set expires_at=null."
                )
            if not output.expires_at_rationale:
                raise ModelRetry(
                    "`expires_at` is set but `expires_at_rationale` is "
                    "empty. Provide a short reason for the decay window "
                    "(e.g. 'funding squeeze unwinds within 24h')."
                )
            if not output.expires_at_citations:
                raise ModelRetry(
                    "`expires_at` is set but `expires_at_citations` is "
                    "empty. Provide ≥1 ToolCitation backing the rationale "
                    "(e.g. get_funding_rate showing the squeeze)."
                )
            _check("expires_at", output.expires_at_citations)

        # Non-no_trade ideas need at least one Confluence with citations.
        if output.direction != "no_trade" and not output.confluences:
            raise ModelRetry(
                "Ideas with direction != 'no_trade' require at least one Confluence with citations. "
                "If higher-TF context doesn't justify a setup, set direction='no_trade'."
            )
        for conf in output.confluences:
            _check(f"confluence {conf.timeframe}", conf.citations)

        # Snapshot numeric verification — every cited value must match the
        # tool's actual output within 0.1% tolerance (or be absent from the
        # output, in which case we assume it's a derived quantity). Closes
        # the "called the right tool but quoted an invented number" gap.
        tool_outputs = _collect_tool_outputs(list(ctx.messages))
        all_cited: list[tuple[str, ToolCitation]] = []
        for c in output.entry_citations:
            all_cited.append(("entry", c))
        for c in output.stop_loss_citations:
            all_cited.append(("stop_loss", c))
        for tgt in output.targets:
            for c in tgt.citations:
                all_cited.append((f"target {tgt.label}", c))
        for conf in output.confluences:
            for c in conf.citations:
                all_cited.append((f"confluence {conf.timeframe}", c))
        for i, cond in enumerate(output.invalidation_conditions):
            for c in cond.citations:
                all_cited.append((f"invalidation_condition[{i}]", c))
        for c in output.expires_at_citations:
            all_cited.append(("expires_at", c))

        for label, cite in all_cited:
            if cite.tool_name == "get_liquidation_heatmap":
                err = _verify_liquidation_citation(
                    label=label,
                    cite=cite,
                    tool_outputs=tool_outputs,
                    direction=getattr(output, "direction", None),
                    confidence=getattr(output, "confidence", None),
                )
                if err:
                    raise ModelRetry(f"{label} citation: {err}")
            mismatches = _verify_snapshot_numerics(cite, tool_outputs)
            if not mismatches:
                continue
            detail = "; ".join(
                f"{key!r}={cited:.6g} not in tool output values {[round(v, 6) for v in real]}"
                for key, cited, real in mismatches
            )
            raise ModelRetry(
                f"{label} citation tool_name={cite.tool_name!r} has snapshot "
                f"mismatches: {detail}. Every numeric value in the snapshot "
                f"must match the corresponding value from the tool's actual "
                f"output (tolerance 0.1%). Re-cite using the real numbers, "
                f"or remove the mismatched keys if you derived the cited "
                f"value (e.g. computed a midpoint) — derived values should "
                f"not appear in snapshot, they belong in rationale."
            )

        # Semantic tag verification — tags like 'lvn_support' require the
        # corresponding tool (get_volume_profile) to have actually detected
        # an LVN this turn. Soft gate: degrade confidence from 'high', log
        # the unsupported tags, surface a warning in risk_notes. Promote to
        # hard gate after observing production logs.
        unsupported_tags = _verify_semantic_tags(list(output.semantic_tags or []), tool_outputs)
        if unsupported_tags:
            ctx.deps.log.info(
                "validator.semantic_tags_unsupported",
                tags=unsupported_tags,
                direction=output.direction,
                symbol=output.symbol,
                timeframe=output.timeframe,
            )
            if output.confidence == "high":
                output.confidence = "medium"
            warning = (
                f"semantic_tags sin estructura verificada este turno: {', '.join(unsupported_tags)}"
            )
            output.risk_notes = f"{output.risk_notes} ({warning})" if output.risk_notes else warning

        # Factor Gate (A.2) — consulta factor_stats_repo y aplica gate
        # progresivo basado en win-rate LCB Bayesian del usuario bajo el
        # régimen actual. Solo se ejecuta si get_multi_tf_confluence fue
        # llamada (sin snapshot no hay factores que evaluar). Errores
        # transitorios de DB se loguean y NO bloquean el trade — el gate
        # es una segunda capa, no debe convertir un infra hiccup en
        # rechazo silencioso.
        factor_snapshot_for_gate = _build_factor_snapshot(
            messages=list(ctx.messages),
            output=output,
        )
        if factor_snapshot_for_gate is not None:
            ctx_dict = factor_snapshot_for_gate.get("context")
            regime_label: str | None = None
            if isinstance(ctx_dict, dict):
                raw_regime = ctx_dict.get("regime_label")
                if isinstance(raw_regime, str):
                    regime_label = raw_regime
            verdict = None
            try:
                async with ctx.deps.session_factory() as session:
                    verdict = await evaluate_factor_gate(
                        session,
                        user_id=ctx.deps.user_id,
                        factor_snapshot=factor_snapshot_for_gate,
                        regime_label=regime_label,
                    )
            except Exception as exc:
                ctx.deps.log.warning(
                    "validator.factor_gate_failed",
                    error=str(exc),
                    user_id=ctx.deps.user_id,
                )

            if verdict is not None:
                if not verdict.passed:
                    blocks_desc = "; ".join(
                        f"{b.factor_name}"
                        f"{('@' + b.factor_tf) if b.factor_tf else ''}"
                        f" (n={b.n_trades}, wr_lcb={b.win_rate_lcb:.2f})"
                        for b in verdict.blocking_factors
                    )
                    ctx.deps.log.info(
                        "validator.factor_gate_hard_veto",
                        blockers=[b.model_dump() for b in verdict.blocking_factors],
                    )
                    raise ModelRetry(
                        f"Factor gate (hard veto): este setup se apoya en "
                        f"factores con historial débil bajo tu régimen "
                        f"actual: {blocks_desc}. Cuando win_rate_lcb < 30% "
                        f"con n ≥ 100, el edge histórico es negativo en "
                        f"esperanza. Opciones: (a) revisa si alguno de esos "
                        f"factores NO está realmente activo aquí (quítalo "
                        f"de semantic_tags o de las confluences citadas); "
                        f"(b) cambia direction='no_trade' y explica qué "
                        f"triggers reabrirían la operativa."
                    )

                if verdict.soft_veto_factors:
                    soft_desc = ", ".join(
                        f"{b.factor_name}"
                        f"{('@' + b.factor_tf) if b.factor_tf else ''}"
                        f" (n={b.n_trades}, wr_lcb={b.win_rate_lcb:.2f})"
                        for b in verdict.soft_veto_factors
                    )
                    ctx.deps.log.info(
                        "validator.factor_gate_soft_veto",
                        factors=[b.model_dump() for b in verdict.soft_veto_factors],
                    )
                    if output.confidence != "low":
                        output.confidence = "low"
                    warning = (
                        f"factor_stats soft veto bajo este régimen: {soft_desc} "
                        f"→ confidence forzada a 'low'"
                    )
                    output.risk_notes = (
                        f"{output.risk_notes} ({warning})" if output.risk_notes else warning
                    )

                if verdict.advisory_factors:
                    ctx.deps.log.info(
                        "validator.factor_gate_advisory",
                        factors=[b.model_dump() for b in verdict.advisory_factors],
                    )

        # Position sizing — requerido para non-no_trade ideas.
        # El system prompt instruye al modelo a calcular position_size_pct +
        # leverage_x desde trader_profile.risk_per_trade_pct. Si los omite,
        # el copilot no puede dimensionar y el usuario va a opera "a ojo".
        if (
            output.direction in ("long", "short")
            and output.entry is not None
            and output.stop_loss is not None
        ):
            if output.position_size_pct is None:
                raise ModelRetry(
                    f"direction='{output.direction}' requiere `position_size_pct`. "
                    f"Calcula: stop_distance_pct = |entry − stop_loss| / entry, "
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
            stop_distance_pct = abs(output.entry - output.stop_loss) / output.entry * 100
            if stop_distance_pct > 0:
                # risk_per_trade_pct implícito = position_size_pct × stop_distance_pct / 100
                # No imponemos un floor exacto; sólo que no sea absurdo (>10%
                # del capital de riesgo en un solo trade).
                implied_risk_pct = (
                    output.position_size_pct * output.leverage_x * stop_distance_pct / 100
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
                contradicts = (output.direction == "long" and aggregate_bias == "bear") or (
                    output.direction == "short" and aggregate_bias == "bull"
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
            and output.stop_loss is not None
            and output.targets
        ):
            risk = abs(output.entry - output.stop_loss)
            if risk == 0:
                raise ModelRetry(
                    f"entry={output.entry} == stop_loss={output.stop_loss} → "
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
                if output.stop_loss >= output.entry:
                    raise ModelRetry(
                        f"direction='long' pero stop_loss={output.stop_loss} ≥ "
                        f"entry={output.entry}. En long el SL va POR DEBAJO del entry."
                    )
            else:  # short
                reward = output.entry - first_tp.price
                if reward <= 0:
                    raise ModelRetry(
                        f"direction='short' pero {first_tp.label}={first_tp.price} ≥ "
                        f"entry={output.entry}. En short el TP va POR DEBAJO del entry."
                    )
                if output.stop_loss <= output.entry:
                    raise ModelRetry(
                        f"direction='short' pero stop_loss={output.stop_loss} ≤ "
                        f"entry={output.entry}. En short el SL va POR ENCIMA del entry."
                    )
            rr = reward / risk
            # B.2 / RM-2: base R:R floor now comes from Settings.min_rr_ratio
            # (single source of truth from M1-Risk sprint); per-symbol
            # slippage buffer is added on top so the post-fill realized
            # ratio still beats the configured base.
            from app.core.config import get_settings

            cfg = get_settings()
            slippage_buffer = cfg.slippage_buffer_r(output.symbol)
            min_rr = cfg.min_rr_ratio + slippage_buffer
            if rr < min_rr:
                raise ModelRetry(
                    f"R:R al primer TP es {rr:.2f}:1 (reward={reward:.2f} / "
                    f"risk={risk:.2f}). Mínimo aceptable {min_rr:.2f}:1 para "
                    f"{output.symbol} ({cfg.min_rr_ratio} base + "
                    f"{slippage_buffer:.2f} de buffer por slippage cripto). "
                    f"Mueve el SL más cerca del entry, ajusta el TP a un "
                    f"nivel más lejano (cita tool_name=get_market_structure), "
                    f"o cambia direction='no_trade'. NO propongas trades con "
                    f"esperanza negativa una vez descontado el slippage real."
                )

            # RM-2: max-leverage hard gate (Settings.max_leverage_per_position).
            # Pure-function gate from app/risk/gates.py — reused so the
            # threshold lives in exactly one place. Reject (not size-reduce)
            # so the LLM knows it must propose a smaller leverage.
            from app.risk.gates import max_leverage_gate

            lev_outcome = max_leverage_gate(output, cfg)
            if not lev_outcome.passed:
                raise ModelRetry(
                    f"Risk gate '{lev_outcome.name}' rechazó este setup: "
                    f"{lev_outcome.reason}. Reduce `leverage_x` a un valor "
                    f"≤ {cfg.max_leverage_per_position:g} y resubmite. La "
                    f"política está fijada en Settings (M1-Risk sprint)."
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
                        {"label": lbl, "price": p, "lvn_center": c} for lbl, p, c in offending
                    ],
                    n_offending=len(offending),
                )
                if output.confidence == "high":
                    output.confidence = "medium"

        # Validator A — confidence-vs-summary alignment (soft-degrade).
        # En TradeIdea el "verdict" vive en summary_es (más largo). Si el
        # modelo emite confidence='high' pero el summary recomienda esperar
        # un trigger antes de operar, recalibramos a 'medium'.
        if output.confidence == "high" and _VERDICT_PAUSE_PATTERN.search(output.summary_es):
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
            and output.stop_loss is not None
            and output.targets
        ):
            # F5.5: construir factor_snapshot desde el último ConfluenceMap
            # del turno + semantic_tags (filtrados al allow-list) +
            # contexto del régimen. None si la tool no se llamó este turn
            # — el trade quedará sin attribution (no fan-out al cerrar).
            factor_snapshot = _build_factor_snapshot(
                messages=list(ctx.messages),
                output=output,
            )
            # Audit fix 2026-05: persistencia movida a `setups/events.py`.
            # El validator delega; la separación deja el validator centrado
            # en contratos y la materialización testeable aisladamente.
            async with ctx.deps.session_factory() as session:
                await persist_trade_idea(
                    session,
                    user_id=ctx.deps.user_id,
                    idea=output,
                    factor_snapshot=factor_snapshot,
                    log=ctx.deps.log,
                )

        return output
