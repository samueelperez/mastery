// SYNC WITH apps/api/app/agent/models.py — keep these aligned by hand for F1.
// F2 will introduce datamodel-code-generator over the OpenAPI schema.

export type Timeframe = "15m" | "1h" | "4h" | "1d"
export type Direction = "long" | "short" | "no_trade"
export type Bias = "bull" | "bear" | "range"
export type Confidence = "low" | "medium" | "high"
export type RegimeLabel =
  | "trending_up"
  | "trending_down"
  | "ranging"
  | "volatile_expansion"

export interface ToolCitation {
  tool_call_id: string
  tool_name: string
  snapshot: Record<string, unknown>
}

export interface Confluence {
  timeframe: Timeframe
  bias: Bias
  /** 1-2 frases de prosa de trader explicando el bias en este TF.
   *  Sustituye al `reasons: string[]` previo (bullets tipo log). */
  narrative: string
  citations: ToolCitation[]
}

export interface MarketRegime {
  label: RegimeLabel
  citations: ToolCitation[]
}

export interface TradeIdeaTarget {
  label: string
  price: number
  rationale: string
  citations: ToolCitation[]
}

export interface BiasAlert {
  /** e.g. ['revenge_trading', 'overtrading'] */
  kinds: string[]
  severity: "low" | "medium" | "high"
  message: string
}

export interface Scenario {
  label: "A" | "B" | "C"
  /** Probabilidad subjetiva 5-90; la suma de scenarios debe ≈100%. */
  probability_pct: number
  description: string
  entry?: number | null
  stop_loss?: number | null
  target?: number | null
}

/** Una `RuleSpec` (mismo shape que `create_alert.spec`) más metadata humana.
 *  El backend valida el shape vía `app.alerts.dsl.RuleSpec` antes de persistir;
 *  la UI puede tratar `spec` como opaco para mostrar (al menos en v1). */
export interface InvalidationCondition {
  spec: Record<string, unknown>
  rationale: string
  citations: ToolCitation[]
}

export interface TradeIdea {
  symbol: string
  timeframe: Timeframe
  direction: Direction
  regime: MarketRegime
  confluences: Confluence[]
  entry: number | null
  entry_rationale: string | null
  entry_citations: ToolCitation[]
  stop_loss: number | null
  stop_loss_rationale: string | null
  stop_loss_citations: ToolCitation[]
  targets: TradeIdeaTarget[]
  /** 0..N condiciones (RuleSpec + rationale + citations) que cancelan el
   *  setup mientras está PENDING. OR-combined globalmente: la primera que
   *  dispare → status `cancelled` con event `invalidated`. */
  invalidation_conditions?: InvalidationCondition[]
  /** Wall-clock expiry opcional. Si el momento pasa sin entry hit, el setup
   *  pasa a `cancelled` con event `invalidated`. ISO-8601 UTC. Solo cuando
   *  el agente identifica una tesis time-sensitive — NUNCA por defecto. */
  expires_at?: string | null
  expires_at_rationale?: string | null
  expires_at_citations?: ToolCitation[]
  /** Mapa de decisión: 2-3 ramas plausibles con probabilidad. Vacío en
   *  no_trade puro o cuando el modelo no genera scenarios. */
  scenarios?: Scenario[]
  position_size_pct: number | null
  leverage_x: number | null
  risk_notes: string
  /** Auto-poblado por el validator del backend cuando detect_bias_patterns
   *  devuelve flags severity=high. La UI lo pinta como banner separado del
   *  análisis técnico (no afecta direction). */
  bias_alert?: BiasAlert | null
  confidence: Confidence
  summary_es: string
}

/**
 * Type guard: a `tool-final_result` part (as emitted by Pydantic AI's vercel-ai
 * bridge) carries the agent's structured output as `input`. We render it as a
 * TradeIdeaCard instead of the generic Tool component.
 */
export function isTradeIdea(input: unknown): input is TradeIdea {
  if (!input || typeof input !== "object") return false
  const v = input as Record<string, unknown>
  // `confidence` se añadió al guard tras un crash en producción: durante
  // streaming, los campos llegan progresivamente y el input parcial pasaba el
  // guard antes de tener confidence, haciendo crashear `.toUpperCase()` en la
  // card. Validar el conjunto completo (campos críticos + confidence) evita
  // renderizar TradeIdeaCard con datos a medio camino.
  return (
    typeof v.symbol === "string" &&
    typeof v.timeframe === "string" &&
    typeof v.direction === "string" &&
    typeof v.summary_es === "string" &&
    typeof v.confidence === "string" &&
    Array.isArray(v.confluences) &&
    Array.isArray(v.targets)
  )
}

export interface KeyLevel {
  label: string
  price: number
  kind: "support" | "resistance" | "invalidation" | "target" | "reference"
}

/** Análisis exploratorio sin trade direccional. Renderizado como prosa de
 * 3 párrafos por BriefAnalysisCard. Distinto de TradeIdea: aquí no hay
 * entry/SL/TP, position_size, scenarios, citations. */
export interface BriefAnalysis {
  symbol: string
  timeframe: Timeframe
  verdict_es: string
  catalyst_es: string
  risk_es: string
  key_levels: KeyLevel[]
  confidence: Confidence
  bias_alert?: BiasAlert | null
}

export function isBriefAnalysis(input: unknown): input is BriefAnalysis {
  if (!input || typeof input !== "object") return false
  const v = input as Record<string, unknown>
  // Discriminante clave vs TradeIdea: verdict_es exclusivo de BriefAnalysis.
  // `confidence` también es required para evitar render parcial durante
  // streaming (mismo motivo que isTradeIdea).
  return (
    typeof v.symbol === "string" &&
    typeof v.timeframe === "string" &&
    typeof v.verdict_es === "string" &&
    typeof v.catalyst_es === "string" &&
    typeof v.risk_es === "string" &&
    typeof v.confidence === "string" &&
    Array.isArray(v.key_levels)
  )
}
