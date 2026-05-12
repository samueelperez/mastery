// PartySocket's higher-level export is bound to the PartyKit room model.
// We just want a reconnecting WebSocket pointed at our FastAPI backend, so we
// import the lower-level `ReconnectingWebSocket` from `partysocket/ws`.
import ReconnectingWebSocket from "partysocket/ws"

import { BEARER_TOKEN_KEY } from "@/lib/auth/auth-client"
import { env } from "@/lib/env"

/** Browsers no permiten Authorization header en WebSocket. Pasamos el
 * BetterAuth bearer token (capturado en login y persistido en localStorage)
 * via query string `?token=…`. El backend lo lee igual que la cookie. */
function readBearerToken(): string | null {
  if (typeof window === "undefined") return null
  return window.localStorage.getItem(BEARER_TOKEN_KEY)
}

function appendAuthToken(url: string): string {
  const token = readBearerToken()
  if (!token) return url
  const sep = url.includes("?") ? "&" : "?"
  return `${url}${sep}token=${encodeURIComponent(token)}`
}

/** Backend message envelope from /ws/market. */
export type MarketWsMessage =
  | { type: "subscribed"; channel: string }
  | { type: "ping" }
  | { type: "kline"; data: KlinePayload }

export interface KlinePayload {
  exchange: string
  symbol: string
  timeframe: string
  ts: string
  o: number
  h: number
  l: number
  c: number
  v: number
  is_closed: boolean
}

export function connectMarketWs(
  symbol: string,
  timeframe: string,
): ReconnectingWebSocket {
  const url = `${env.wsUrl}/ws/market?symbol=${encodeURIComponent(symbol)}&tf=${encodeURIComponent(timeframe)}`
  // Defaults: max 30 retry attempts, exponential backoff up to ~30s, message buffering.
  return new ReconnectingWebSocket(appendAuthToken(url))
}

/** Backend envelope from /ws/alerts. `data` matches alert_events row + rule context. */
export type AlertWsMessage =
  | { type: "subscribed"; channel: string }
  | { type: "ping" }
  | { type: "alert_event"; data: AlertEventPayload }

export interface AlertEventPayload {
  event_id: number
  rule_id: string | null
  rule_name: string
  fired_at: string
  kind: "rule_match" | "bias_promoted"
  severity: "low" | "medium" | "high"
  snapshot: Record<string, unknown>
}

export function connectAlertsWs(userId = "me"): ReconnectingWebSocket {
  const url = `${env.wsUrl}/ws/alerts?user_id=${encodeURIComponent(userId)}`
  return new ReconnectingWebSocket(appendAuthToken(url))
}

/** Backend envelope from /ws/reviews. `data` matches setup_reviews row +
 * setup context (symbol, timeframe, side). */
export type ReviewWsMessage =
  | { type: "subscribed"; channel: string }
  | { type: "ping" }
  | { type: "trade_review"; data: TradeReviewPayload }
  | { type: "post_mortem"; data: PostMortemPayload }

export interface TradeReviewPayload {
  review_id: string
  setup_id: string
  symbol: string
  timeframe: string
  side: "long" | "short"
  trigger_kind:
    | "entry_hit"
    | "tp_partial"
    | "time_elapsed"
    | "price_move"
    | "approaching_sl"
    | "regime_change"
  trigger_payload: Record<string, unknown>
  current_state: "on_track" | "at_risk" | "reversing"
  recommendation: "hold" | "tighten_sl" | "partial_close" | "exit_now"
  summary: string
  rationale: string
  citations: { tool_name: string; snapshot: Record<string, unknown> }[]
  price_at_review: number
  created_at: string
}

/** Backend envelope cuando un setup cierra (SL hit o TP-all). Trigger
 * exclusivo del post_mortem_dispatcher; el review_dispatcher nunca emite
 * estos `trigger_kind`. */
export interface PostMortemPayload {
  post_mortem_id: string
  trade_id: string
  symbol: string
  timeframe: string
  side: "long" | "short"
  trigger_kind: "setup_closed_sl" | "setup_closed_tp"
  outcome: "win" | "loss" | "breakeven" | "partial_win"
  r_multiple: number
  verdict: "thesis_held" | "thesis_broken" | "execution_error" | "noise"
  confidence_calibration: "over" | "under" | "calibrated"
  success_factors: string[]
  failure_factors: string[]
  lesson_es: string
  counterfactual_es: string | null
  mfe_mae: {
    mfe_r: number
    mae_r: number
    mfe_at: string
    mae_at: string
    time_to_mfe_h: number
    time_to_mae_h: number
    exit_efficiency_pct: number | null
  } | null
  citations: { tool_name: string; snapshot: Record<string, unknown> }[]
  created_at: string
}

export function connectReviewsWs(): ReconnectingWebSocket {
  const url = `${env.wsUrl}/ws/reviews`
  return new ReconnectingWebSocket(appendAuthToken(url))
}
