// SYNC con apps/api/app/agent/tools/{indicators,confluence,structure,
// volume_profile,perps_data,correlation}.py + apps/api/app/agent/tools/
// _envelope.py (ToolResult envelope). F2 introducirá datamodel-code-generator
// sobre el OpenAPI; por ahora a mano.

import type { Timeframe } from "./chat-types"

// ---------------------------------------------------------------------------
// Envelope (ToolResult[T] del backend)
// ---------------------------------------------------------------------------

export interface Provenance {
  source: string
  as_of: string // ISO datetime
  rows: number
  warnings: string[]
}

export interface ToolResultEnvelope<T> {
  data: T
  provenance: Provenance
}

// ---------------------------------------------------------------------------
// get_indicators output
// ---------------------------------------------------------------------------

export interface IndicatorPanelDTO {
  asof: string
  series_tail: Record<string, (number | null)[]>
  latest: Record<string, unknown>
}

/** Spec que el agente envía como `input.indicators[]` al tool — base para
 *  inferir qué EMAs/SMAs/etc. debemos dibujar en el chart. */
export interface IndicatorSpecDTO {
  name: string
  length?: number | null
}

// ---------------------------------------------------------------------------
// get_multi_tf_confluence output
// ---------------------------------------------------------------------------

export interface TimeframeBiasDTO {
  timeframe: Timeframe
  bias: "bull" | "bear" | "range"
  score: number
  reasons: string[]
  last_close: number | null
  ema_21: number | null
  ema_55: number | null
  ema_200: number | null
}

export interface ConfluenceMapDTO {
  by_tf: TimeframeBiasDTO[]
  aggregate_bias: "bull" | "bear" | "range"
  aggregate_agreement_pct: number
}

// ---------------------------------------------------------------------------
// get_market_structure output
// ---------------------------------------------------------------------------

export interface PivotDTO {
  ts: string
  price: number
  kind: "high" | "low"
}

export interface LevelDTO {
  price: number
  touches: number
  last_touch_ts: string
}

export interface MarketStructureDTO {
  swing_highs: PivotDTO[]
  swing_lows: PivotDTO[]
  support: LevelDTO[]
  resistance: LevelDTO[]
  trend_label: "HH_HL" | "LH_LL" | "mixed" | "indeterminate"
  current_close: number | null
  atr_used: number | null
}

// ---------------------------------------------------------------------------
// get_volume_profile output
// ---------------------------------------------------------------------------

export interface VolumeNodeDTO {
  price: number
  volume: number
  pct_of_poc: number
}

export interface VolumeProfileDTO {
  symbol: string
  timeframe: Timeframe
  lookback_bars: number
  bins: number
  poc_price: number
  poc_volume: number
  high_volume_nodes: VolumeNodeDTO[]
  low_volume_nodes: VolumeNodeDTO[]
  range_low: number
  range_high: number
  interpretation: string
}

// ---------------------------------------------------------------------------
// get_funding_rate output
// ---------------------------------------------------------------------------

export type FundingBias = "long_pays" | "short_pays" | "neutral"

export interface FundingRateDTO {
  symbol: string
  current_rate_pct: number
  next_funding_ts: string
  avg_7d_pct: number
  cumulative_7d_pct: number
  bias: FundingBias
  interpretation: string
}

// ---------------------------------------------------------------------------
// get_open_interest output
// ---------------------------------------------------------------------------

export type OiTrend = "rising" | "falling" | "stable"

export interface OpenInterestDTO {
  symbol: string
  current_oi_base: number
  current_oi_usdt: number
  delta_24h_pct: number
  trend_7d: OiTrend
  interpretation: string
}

// ---------------------------------------------------------------------------
// get_btc_correlation output
// ---------------------------------------------------------------------------

export interface BtcCorrelationDTO {
  symbol: string
  reference: string // siempre "BTCUSDT" hoy
  timeframe: Timeframe
  lookback_bars: number
  pearson: number
  bias_weight_factor: number
  interpretation: string
}

// ---------------------------------------------------------------------------
// Type guards — usados por el bridge para discriminar tool-outputs en runtime
// ---------------------------------------------------------------------------

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null
}

/** Acepta tanto `{ data, provenance }` como el `data` desnudo (defensivo). */
export function unwrapToolResult<T>(value: unknown): T | null {
  if (!isObj(value)) return null
  if ("data" in value && "provenance" in value) {
    return value.data as T
  }
  return value as T
}

export function isIndicatorPanel(v: unknown): v is IndicatorPanelDTO {
  return (
    isObj(v) &&
    typeof v.asof === "string" &&
    isObj(v.series_tail) &&
    isObj(v.latest)
  )
}

export function isConfluenceMap(v: unknown): v is ConfluenceMapDTO {
  return (
    isObj(v) &&
    Array.isArray(v.by_tf) &&
    typeof v.aggregate_bias === "string"
  )
}

export function isMarketStructure(v: unknown): v is MarketStructureDTO {
  return (
    isObj(v) &&
    Array.isArray(v.swing_highs) &&
    Array.isArray(v.swing_lows) &&
    Array.isArray(v.support) &&
    Array.isArray(v.resistance)
  )
}

export function isVolumeProfile(v: unknown): v is VolumeProfileDTO {
  return (
    isObj(v) &&
    typeof v.poc_price === "number" &&
    Array.isArray(v.high_volume_nodes) &&
    Array.isArray(v.low_volume_nodes)
  )
}

export function isFundingRate(v: unknown): v is FundingRateDTO {
  if (!isObj(v)) return false
  if (typeof v.current_rate_pct !== "number") return false
  return v.bias === "long_pays" || v.bias === "short_pays" || v.bias === "neutral"
}

export function isOpenInterest(v: unknown): v is OpenInterestDTO {
  if (!isObj(v)) return false
  if (typeof v.delta_24h_pct !== "number") return false
  return v.trend_7d === "rising" || v.trend_7d === "falling" || v.trend_7d === "stable"
}

export function isBtcCorrelation(v: unknown): v is BtcCorrelationDTO {
  return (
    isObj(v) &&
    typeof v.pearson === "number" &&
    typeof v.bias_weight_factor === "number" &&
    v.reference === "BTCUSDT"
  )
}
