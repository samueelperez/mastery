"""Pydantic schemas the agent's structured output must conform to.

These are the contract between the LLM and the validator: every quantitative
claim (entry, invalidation, target prices) MUST carry citations to tool calls
that produced the underlying data. The validator at `app.agent.validators`
enforces this — the prompt only describes it.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

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
    invalidation: float | None = None
    target: float | None = None


class TradeIdea(BaseModel):
    """The agent's structured analysis output.

    Use direction='no_trade' when conditions don't justify a setup; the validator
    permits empty entry/invalidation/targets/confluences in that case.
    """

    symbol: str
    timeframe: Timeframe
    direction: Direction
    regime: MarketRegime
    confluences: list[Confluence] = Field(default_factory=list)

    entry: float | None = None
    entry_rationale: str | None = None
    entry_citations: list[ToolCitation] = Field(default_factory=list)

    invalidation: float | None = Field(default=None, description="Stop loss; logical, not %.")
    invalidation_rationale: str | None = None
    invalidation_citations: list[ToolCitation] = Field(default_factory=list)

    targets: list[TradeIdeaTarget] = Field(default_factory=list)

    scenarios: list[Scenario] = Field(
        default_factory=list,
        max_length=3,
        description=(
            "Mapa de decisión: 2-3 ramas plausibles del mercado con "
            "probabilidad asignada. Scenario A coincide con el setup "
            "principal (entry/invalidation/targets[0]); B y C son las "
            "alternativas razonables. Para análisis direccionales "
            "accionables debes producir ≥2. Para no_trade puede dejarse "
            "vacío O contener 'qué triggers reabren la operativa'."
        ),
    )

    # Sizing — ausente del schema antes del Sprint B (auditoría 2026-05).
    # Se calcula desde trader_profile: risk_per_trade_pct × stop_distance.
    # Si direction='no_trade' o invalidation None, ambos quedan None.
    position_size_pct: float | None = Field(
        default=None,
        ge=0.0,
        le=2000.0,
        description=(
            "% NOTIONAL del equity ANTES de aplicar leverage. PUEDE SUPERAR "
            "100 cuando el stop es ajustado y se compensa con leverage_x. "
            "Calcular: stop_distance_pct = |entry-invalidation| / entry × 100; "
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
