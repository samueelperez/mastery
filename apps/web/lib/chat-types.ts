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
  reasons: string[]
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

export interface TradeIdea {
  symbol: string
  timeframe: Timeframe
  direction: Direction
  regime: MarketRegime
  confluences: Confluence[]
  entry: number | null
  entry_rationale: string | null
  entry_citations: ToolCitation[]
  invalidation: number | null
  invalidation_rationale: string | null
  invalidation_citations: ToolCitation[]
  targets: TradeIdeaTarget[]
  risk_notes: string
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
  return (
    typeof v.symbol === "string" &&
    typeof v.timeframe === "string" &&
    typeof v.direction === "string" &&
    typeof v.summary_es === "string" &&
    Array.isArray(v.confluences) &&
    Array.isArray(v.targets)
  )
}
