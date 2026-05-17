"""Pydantic schemas the agent's structured output must conform to.

These are the contract between the LLM and the validator: every quantitative
claim (entry, stop_loss, target prices) MUST carry citations to tool calls
that produced the underlying data. The validator at `app.agent.validators`
enforces this — the prompt only describes it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.alerts.dsl import RuleSpec

Timeframe = Literal["15m", "1h", "4h", "1d"]
Direction = Literal["long", "short", "no_trade"]
Bias = Literal["bull", "bear", "range"]
Confidence = Literal["low", "medium", "high"]
RegimeLabel = Literal["trending_up", "trending_down", "ranging", "volatile_expansion"]


class ToolCitation(BaseModel):
    """Pointer to the tool call whose output backs a numeric claim.

    The validator discriminates by `tool_name` (LLMs can't reliably echo opaque
    provider IDs like `toolu_vrtx_...`). For citations that reference a stable
    handle in the tool output — `run_id` (run_backtest, get_strategy_metrics)
    or `trade_ids` (get_similar_past_trades) — the validator additionally
    checks that the cited handle was actually returned by a tool this turn.
    """

    tool_name: str = Field(
        ...,
        description="Literal function name you called this turn — discriminator.",
    )
    snapshot: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Excerpt of the tool output: the value being cited "
            "(e.g. {'ema_55': 67234.1} or {'run_id': '<uuid>', 'dsr': 0.7})."
        ),
    )
    tool_call_id: str | None = Field(
        default=None,
        description="Optional, best-effort. UI uses it for grouping; validator does NOT check it.",
    )


class Confluence(BaseModel):
    timeframe: Timeframe
    bias: Bias
    narrative: str = Field(
        ...,
        max_length=240,
        description=(
            "1-2 frases de prosa de trader explicando el bias en este TF. "
            "Combina los datos mecánicos (EMA stack, ADX, RSI, regime) en una "
            "lectura humana. Ej: 'EMA21>55>200 con close 0.8 ATR sobre la media "
            "— alineación bull intacta. ADX 32 confirma trend, no agotamiento, "
            "pese al RSI 69.9 ya estirado.' Cero bullets, cero meta-comentario."
        ),
    )
    citations: list[ToolCitation] = Field(default_factory=list)


class MarketRegime(BaseModel):
    label: RegimeLabel
    citations: list[ToolCitation] = Field(default_factory=list)


class BiasAlert(BaseModel):
    """Aviso de patrones psicológicos detectados en el journal — informativo,
    NO bloqueante. La UI lo pinta como banner separado del análisis técnico.

    Auto-poblado por el validator a partir del output de detect_bias_patterns;
    el LLM no necesita generar este campo.
    """

    kinds: list[str] = Field(
        ...,
        min_length=1,
        description="e.g. ['revenge_trading', 'overtrading']",
    )
    severity: Literal["low", "medium", "high"]
    message: str = Field(..., max_length=300)


class TradeIdeaTarget(BaseModel):
    label: str = Field(..., description="e.g. 'TP1', 'TP2', 'TP_runner'")
    price: float
    rationale: str
    citations: list[ToolCitation] = Field(default_factory=list)


# Factor Gate (A.2) ----------------------------------------------------------
#
# `evaluate_factor_gate` returns a `GateVerdict` summarizing whether the
# factors a TradeIdea relies on have weak historical performance for this
# user. Progressive thresholds keyed off Bayesian win-rate LCB and sample size
# — see `factor_stats_repo.evaluate_factor_gate` for the policy table.

FactorKind = Literal["deterministic", "semantic"]
GateSeverity = Literal["advisory", "soft_veto", "hard_veto"]


class FactorBlock(BaseModel):
    factor_name: str
    factor_tf: str | None
    factor_kind: FactorKind
    n_trades: int
    win_rate_lcb: float
    severity: GateSeverity


class GateVerdict(BaseModel):
    passed: bool = Field(
        ...,
        description="False if any hard_veto was hit. True otherwise (including soft_veto cases — those flow as confidence degradations).",
    )
    blocking_factors: list[FactorBlock] = Field(default_factory=list)
    soft_veto_factors: list[FactorBlock] = Field(default_factory=list)
    advisory_factors: list[FactorBlock] = Field(default_factory=list)


class InvalidationCondition(BaseModel):
    """Rule that auto-cancels a PENDING setup before it ever enters.

    Reuses the `RuleSpec` shape (price + indicator + cross_above/below
    operators) from `app.alerts.dsl` so `SetupRuntime` can run the same
    `evaluate_rule` against an enriched OHLCV+indicators panel that the
    AlertsRuntime uses. OR-combined globally across the list at the
    TradeIdea level; AND-within-condition is handled by `RuleSpec.logic`.

    The citation contract MIRRORS the rest of the system: every threshold
    in `spec` must trace back to a tool the agent called this turn, with
    at least one ToolCitation in `citations` referencing that tool by
    `tool_name`. The validator (`app.agent.validators`) enforces this.

    Cross-symbol policy: usually `spec.symbol == idea.symbol`, but for
    non-BTC altcoins it's legal to set `spec.symbol == "BTCUSDT"` IF a
    citation in this condition points to `get_btc_correlation` — the
    correlation gate prevents lazy "throw BTC at every alt" invalidations.
    """

    spec: RuleSpec
    rationale: str = Field(
        ...,
        min_length=1,
        max_length=240,
        description=(
            "Frase corta justificando POR QUÉ esta condición invalida la "
            "tesis. Ej: 'Si el rango 4h pierde 63500 con cierre, la "
            "estructura alcista se rompe — descartar long.'"
        ),
    )
    citations: list[ToolCitation] = Field(
        ...,
        min_length=1,
        description="≥1 ToolCitation pointing to the tool that produced the threshold.",
    )


class KeyLevel(BaseModel):
    """Nivel de precio relevante para overlay en chart y como ancla del análisis.

    Mantener ≤4 por análisis para evitar sobrecarga visual. Distinto de
    `TradeIdeaTarget` (que vive solo dentro de TradeIdea direccional): este
    se usa en `BriefAnalysis` exploratorio para sugerir niveles a vigilar.
    """

    label: str = Field(
        ...,
        max_length=32,
        description="Etiqueta corta del nivel. Ej: 'EMA200 1d', 'POC', 'mín. reciente'.",
    )
    price: float
    kind: Literal["support", "resistance", "invalidation", "target", "reference"]


class Scenario(BaseModel):
    """Una rama plausible del mercado con asignación de probabilidad.

    Permite expresar "60% pullback, 30% break, 10% invalidación" en lugar de
    un único path determinista. Forma de pensar de un trader senior.
    """

    label: Literal["A", "B", "C"]
    probability_pct: int = Field(
        ...,
        ge=5,
        le=90,
        description=(
            "Probabilidad subjetiva asignada a esta rama (5-90). La suma de "
            "todos los scenarios del TradeIdea debe ≈100% (margen ±10% — "
            "no enforced en el validator pero esperable de un trader honesto)."
        ),
    )
    description: str = Field(
        ...,
        max_length=200,
        description=(
            "Trigger + consecuencia en una frase. Ej: 'Pullback al EMA21 "
            "78.8k → entry tras confirmar reversa con vela alcista en 4h'."
        ),
    )
    entry: float | None = None
    stop_loss: float | None = None
    target: float | None = None


class TradeIdea(BaseModel):
    """The agent's structured analysis output.

    Use direction='no_trade' when conditions don't justify a setup; the validator
    permits empty entry/stop_loss/targets/confluences in that case.
    """

    symbol: str
    timeframe: Timeframe
    direction: Direction
    regime: MarketRegime
    confluences: list[Confluence] = Field(default_factory=list)

    entry: float | None = None
    entry_rationale: str | None = None
    entry_citations: list[ToolCitation] = Field(default_factory=list)

    stop_loss: float | None = Field(default=None, description="Stop loss; logical price, not %.")
    stop_loss_rationale: str | None = None
    stop_loss_citations: list[ToolCitation] = Field(default_factory=list)

    targets: list[TradeIdeaTarget] = Field(default_factory=list)

    # Pre-entry auto-invalidation. While `pending`, SetupRuntime evaluates
    # each `RuleSpec` against an enriched OHLCV+indicator panel on candle
    # close; if ANY fires, the setup transitions to `cancelled` with event
    # `invalidated`. 0..5 conditions (cap to keep evaluator work bounded);
    # OR-combined globally — AND-within-condition lives in RuleSpec.logic.
    # See `InvalidationCondition` for the citation contract.
    invalidation_conditions: list[InvalidationCondition] = Field(
        default_factory=list,
        max_length=5,
        description=(
            "Condiciones que CANCELAN el setup mientras está PENDING. "
            "Cada una es un RuleSpec (price/indicator/regime) + rationale "
            "+ ≥1 citation. La primera que dispare → status `cancelled` "
            "con event `invalidated`. NO duplicar el stop_loss aquí — "
            "este campo es PRE-ENTRY; el SL solo aplica una vez ACTIVE."
        ),
    )

    # Wall-clock decay. Optional — emit ONLY when the thesis is genuinely
    # time-sensitive (funding squeeze with 24h window, pre-FOMC, news
    # catalyst with stale-by date). For swing setups that may legitimately
    # wait days for entry, LEAVE NULL. The validator enforces tz-aware UTC,
    # future timestamp, and rationale+citations when set.
    expires_at: datetime | None = Field(
        default=None,
        description=(
            "Wall-clock decay (ISO-8601 UTC, future). Solo cuando la tesis "
            "es time-sensitive — NUNCA por defecto en swing setups."
        ),
    )
    expires_at_rationale: str | None = Field(
        default=None,
        max_length=240,
        description="Required when `expires_at` is set.",
    )
    expires_at_citations: list[ToolCitation] = Field(
        default_factory=list,
        description="≥1 ToolCitation required when `expires_at` is set.",
    )

    scenarios: list[Scenario] = Field(
        default_factory=list,
        max_length=3,
        description=(
            "Mapa de decisión: 2-3 ramas plausibles del mercado con "
            "probabilidad asignada. Scenario A coincide con el setup "
            "principal (entry/stop_loss/targets[0]); B y C son las "
            "alternativas razonables. Para análisis direccionales "
            "accionables debes producir ≥2. Para no_trade puede dejarse "
            "vacío O contener 'qué triggers reabren la operativa'."
        ),
    )

    # Sizing — ausente del schema antes del Sprint B (auditoría 2026-05).
    # Se calcula desde trader_profile: risk_per_trade_pct × stop_distance.
    # Si direction='no_trade' o stop_loss None, ambos quedan None.
    position_size_pct: float | None = Field(
        default=None,
        ge=0.0,
        le=2000.0,
        description=(
            "% NOTIONAL del equity ANTES de aplicar leverage. PUEDE SUPERAR "
            "100 cuando el stop es ajustado y se compensa con leverage_x. "
            "Calcular: stop_distance_pct = |entry-stop_loss| / entry × 100; "
            "position_size_pct = risk_per_trade_pct / stop_distance_pct × 100. "
            "EMITIR tal cual del cálculo — NUNCA dividir entre leverage_x. "
            "Ej: stop 0.564% → position_size_pct=177.3, leverage_x=2 (no 88.6). "
            "Si direction='no_trade', dejar null."
        ),
    )
    leverage_x: float | None = Field(
        default=None,
        ge=1.0,
        description=(
            "Apalancamiento sugerido (1×, 2×, 3×…). NUNCA superar "
            "trader_profile.max_leverage. En setups con ATR alto o R:R justo, "
            "preferir bajo (1-2×). En setups con confluencia fuerte y SL "
            "ajustado, hasta el max permitido."
        ),
    )

    risk_notes: str = Field(
        ...,
        description="Slippage, funding, leverage caveats. Required for every idea.",
    )
    bias_alert: BiasAlert | None = Field(
        default=None,
        description=(
            "Aviso conductual auto-poblado por el validator a partir de la "
            "tool detect_bias_patterns. NO afecta `direction`. La UI lo pinta "
            "como banner separado del análisis técnico — el LLM no necesita "
            "generarlo manualmente."
        ),
    )
    confidence: Confidence
    summary_es: str = Field(
        ...,
        description=(
            "Análisis ejecutivo en 4-6 frases (≤1100 caracteres). Cubre, en "
            "este orden y SIN headers ni bullets:\n"
            "1) VEREDICTO: qué hacer y dónde, en lenguaje natural ('entra en "
            "pullback a EMA21 ~78.7k, SL bajo 77k, TP1 80.5k').\n"
            "2) CATALYST: la 1-2 razones decisivas que lo hacen válido AHORA "
            "(dato concreto, no genérico — 'HH-HL intacto en 4h con ADX 32 "
            "expandiendo').\n"
            "3) CONFLICTO: qué NO acompaña ('el daily aún en rango con "
            "EMA55<EMA200, RSI ya en 69.9').\n"
            "4) RIESGO PRINCIPAL: uno solo, el ineludible.\n"
            "Prohibido: meta-comentario tipo 'voy a sintetizar', 'let me "
            "check', 'now I have all the data'. NO repetir cifras que ya "
            "están en niveles. NO MAYÚSCULAS para alertar. Si hay sesgo "
            "psicológico activo, va SOLO en risk_notes. Lenguaje: español de "
            "trader, denso pero legible."
        ),
        max_length=1100,
    )
    # F5.5 — tags semánticos opcionales para agregación en el post-mortem.
    # Vocabulario controlado (validator enforce): patrones que el agente
    # identifica explícitamente y que NO están capturados en el scorer
    # determinístico (ej. "lvn_support", "fvg_fill", "vwap_reclaim",
    # "btc_correlation_breakdown"). Cero tags es perfectamente válido.
    semantic_tags: list[str] = Field(
        default_factory=list,
        max_length=8,
        description=(
            "Lista opcional de tags semánticos del setup para análisis "
            "agregado posterior. Ej: ['lvn_support', 'fvg_fill', "
            "'vwap_reclaim']. Vocabulario controlado por el validator; "
            "tags no reconocidos se descartan. NO duplicar lo que el "
            "scorer determinístico ya captura (ema_stack, rsi, volumen)."
        ),
    )


ReviewState = Literal["on_track", "at_risk", "reversing"]
ReviewRecommendation = Literal["hold", "tighten_sl", "partial_close", "exit_now"]
TriggerKind = Literal[
    "entry_hit",
    "tp_partial",
    "time_elapsed",
    "price_move",
    "approaching_sl",
    "regime_change",
    # F5.5 — terminales para post-mortem (handled by post_mortem_dispatcher,
    # nunca por review_dispatcher). Compartidos con setup_reviews.trigger_kind
    # vía migración 014 para que telemetry y analytics traten los dos
    # subsistemas uniformemente.
    "setup_closed_sl",
    "setup_closed_tp",
    # User-initiated review from the diario panel "Analizar" button.
    # Bypasses cooldown + status restrictions but still respects cap +
    # concurrency semaphore. Migration 030.
    "manual_request",
]


# F5.5 — Post-mortem output types ---------------------------------------------

PostMortemVerdict = Literal[
    "thesis_held",  # ganó el trade y la tesis se cumplió
    "thesis_broken",  # algún factor crítico se invirtió
    "execution_error",  # tesis correcta pero entry/SL/sizing fueron malos
    "noise",  # resultado dominado por ruido/wicks
]
ConfidenceCalibration = Literal["over", "under", "calibrated"]
PostMortemExitReason = Literal["sl_hit", "tp_hit", "manual_close", "time_stop"]
PostMortemOutcome = Literal["win", "loss", "breakeven", "partial_win"]


class TradeReview(BaseModel):
    """Post-entry review de un setup ACTIVE — advisory, NUNCA ejecuta.

    Lo emite el `review_agent` (secundario al main agent) cuando el
    SetupRuntime detecta un trigger relevante: entry_hit, tp parcial, tiempo
    transcurrido desde entry, movimiento de precio significativo, proximidad
    al SL, o cambio de régimen.

    Coherencia state↔recommendation (enforced por validator):
    - `current_state="reversing"` con `recommendation="hold"` → ModelRetry.
      Si la tesis se está rompiendo, no se mantiene en silencio.
    - `current_state="on_track"` con `recommendation="exit_now"` → ModelRetry.
      Si el trade va bien, no se sale precipitadamente.

    El agente NO debe recalcular entry ni stop_loss aquí (son fijos del setup
    original). Solo evalúa el estado actual y sugiere acción sobre la posición.
    """

    summary: str = Field(
        ...,
        max_length=400,
        description=(
            "2-3 frases en español de trader que describen el estado actual "
            "del trade. Ej: 'Estructura HH-HL intacta en 4h tras entry. RSI "
            "62 sin agotamiento, OI subiendo. Precio a +1.2R sin tocar TPs.'"
        ),
    )
    current_state: ReviewState = Field(
        ...,
        description=(
            "on_track: la tesis se mantiene, precio acompaña. "
            "at_risk: deterioro estructural o momentum, SL aún válido. "
            "reversing: BoS contrario, regime cambia, precio dentro de LVN "
            "bajista — la tesis se está rompiendo."
        ),
    )
    recommendation: ReviewRecommendation = Field(
        ...,
        description=(
            "hold: no tocar, dejar correr al SL/TP. "
            "tighten_sl: subir el SL a breakeven o trail (proteger ganancia). "
            "partial_close: tomar parcial ahora aunque no haya tocado TP. "
            "exit_now: cerrar la posición completa antes de SL/TP. "
            "Conservadurismo asimétrico: ante duda hold>partial_close>exit_now."
        ),
    )
    rationale: str = Field(
        ...,
        max_length=600,
        description=(
            "Por qué esta recomendación, con cifras concretas. ≥1 citation "
            "obligatoria. Ej: 'EMA21 4h actúa como soporte dinámico (78.2k) "
            "y precio aún 1.8% sobre ella. ADX 28 expandiendo confirma "
            "trend. No hay razón para tocar el setup todavía.'"
        ),
    )
    citations: list[ToolCitation] = Field(
        ...,
        min_length=1,
        description=(
            "≥1 ToolCitation respaldando rationale. Discriminador por "
            "tool_name (mismo contrato que TradeIdea)."
        ),
    )


class PostMortem(BaseModel):
    """Análisis terminal de un trade cerrado. Output del `post_mortem_agent`
    (3er agente independiente, distinto del main y del review).

    Trigger: `setup_runtime` detecta SL hit o TP-all hit → dispatcher invoca
    el post_mortem_agent con el `factor_snapshot` original + `mfe_mae`
    computado + OHLCV ventana entry→exit + tesis verbatim (summary_es_full,
    confluences, scenarios).

    El agente debe juzgar HONESTAMENTE — sin sesgo de outcome. Es legítimo
    decir "noise" si una vela de wick clavó el SL sin que la tesis se
    rompiera, o "execution_error" si la tesis funcionó pero el SL estaba
    demasiado ajustado. NO se trata de buscar culpables; se trata de
    aprender qué factores predicen consistentemente.

    `failure_factors` y `success_factors` deben ser **claves exactas** del
    `factor_snapshot` del trade (el system prompt enumera el vocabulario
    disponible). Esto permite mapear 1:1 a `factor_outcomes` para agregar
    win-rate por factor.
    """

    setup_id: str
    verdict: PostMortemVerdict
    failure_factors: list[str] = Field(
        default_factory=list,
        max_length=5,
        description=(
            "Factores del factor_snapshot que NO sostuvieron la tesis. Claves "
            "exactas (ej. 'ema_stack@1h', 'lvn_support'). Vacía si verdict ∈ "
            "{'thesis_held', 'noise'}."
        ),
    )
    success_factors: list[str] = Field(
        default_factory=list,
        max_length=5,
        description=("Factores que SÍ funcionaron. Claves exactas del factor_snapshot."),
    )
    lesson_es: str = Field(
        ...,
        min_length=40,
        max_length=400,
        description=(
            "Una frase accionable en español. NO descripción del trade — "
            "lección extrapolable. Ej: 'En régimen ranging, ema_stack@1h por "
            "sí solo no basta — exigir confirmación con volume > 1.3× promedio.'"
        ),
    )
    confidence_calibration: ConfidenceCalibration = Field(
        ...,
        description=(
            "over: el setup era confidence=high pero cerró en loss sin que la "
            "tesis se rompiera (sobre-confianza). "
            "under: confidence=low pero el trade ganó claramente (sub-confianza). "
            "calibrated: outcome y confianza alineadas."
        ),
    )
    counterfactual_es: str | None = Field(
        default=None,
        max_length=400,
        description=(
            "Opcional. Una frase tipo 'si hubieras X en lugar de Y...'. Ej: "
            "'SL en 76.2 (soporte estructural 4h) en lugar de 77.0 habría "
            "evitado el wick — el trade habría tocado TP1.' Omitir si verdict "
            "= 'noise' o si no hay alternativa identificable."
        ),
    )
    citations: list[ToolCitation] = Field(
        ...,
        min_length=1,
        description=(
            "≥1 ToolCitation por las tools usadas para verificar la lectura "
            "técnica POSTERIOR (OHLCV en ventana entry→exit, structure, etc). "
            "El post-mortem NO debe llamar confluence — está auditándolo."
        ),
    )


class BriefAnalysis(BaseModel):
    """Análisis exploratorio sin trade direccional. 3 campos cortos que el
    frontend renderiza como prosa de 3 párrafos.

    Cuándo usar este modelo:
    - El usuario pide análisis exploratorio sin pedir trade idea ("analiza X",
      "qué piensas de Y", "cómo está el mercado", "estructura de Z").

    Cuándo NO usar:
    - Setup accionable explícitamente pedido → `TradeIdea`.
    - Pregunta definitional ("qué es RSI", "explica MACD") → string passthrough.

    El validator enforce: prohibido nombres de tool ("get_funding_rate") y
    prohibido markdown headers/énfasis (##, **) en los tres textos.
    """

    symbol: str
    timeframe: Timeframe

    verdict_es: str = Field(
        ...,
        max_length=200,
        description=(
            "VEREDICTO contundente al inicio. Qué hacer YA, en MÁXIMO 2 "
            "frases. Sin matices, sin 'se podría considerar', sin 'por otro "
            "lado'. Ej: 'No compres BTC aquí. Espera pullback a 79.0–78.4.' "
            "PROHIBIDO: nombres de tools, headers de markdown."
        ),
    )
    catalyst_es: str = Field(
        ...,
        max_length=600,
        description=(
            "Las razones DECISIVAS con cifras concretas, integradas en "
            "prosa. DEBE referenciar al menos 3 fuentes ortogonales del "
            "análisis (estructura/momentum + volumen + derivados o "
            "correlación) — pero NUNCA nombrando tools. Aquí caben los "
            "matices balanceados ('aunque el daily aún resiste'). MÁXIMO "
            "3 frases. Si necesitas más, comprime: una fuente por frase, "
            "una cifra por fuente. NO listes 5 cifras si 3 cuentan la "
            "historia. Ej: 'Estructura HH-HL intacta con OI +2% confirmando "
            "convicción, pero precio a +2 ATR de la media (zona de "
            "agotamiento) y vacío de volumen entre 79.4-80.6 invita "
            "reversa rápida.'"
        ),
    )
    risk_es: str = Field(
        ...,
        max_length=160,
        description=(
            "Qué invalida la lectura, en 1 frase. El nivel o evento concreto "
            "que cambia el cuadro. Ej: 'Pierde 78.0 sin recuperarse y la "
            "estructura alcista se rompe — replantear tesis bear.'"
        ),
    )
    key_levels: list[KeyLevel] = Field(
        default_factory=list,
        max_length=4,
        description=(
            "Niveles ancla del análisis para overlay en chart. Ej: zona de "
            "compra como support, EMA200 como resistance, mín. reciente "
            "como invalidation. Si no hay ninguno claramente accionable, "
            "lista vacía."
        ),
    )
    confidence: Confidence
    bias_alert: BiasAlert | None = Field(
        default=None,
        description=(
            "Auto-poblado por validator. Misma lógica que TradeIdea: si el "
            "usuario pide explícitamente revisión psicológica y el journal "
            "muestra patrones severity=high, banner separado en la UI."
        ),
    )
