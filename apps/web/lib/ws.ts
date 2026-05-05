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
