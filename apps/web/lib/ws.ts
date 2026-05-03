// PartySocket's higher-level export is bound to the PartyKit room model.
// We just want a reconnecting WebSocket pointed at our FastAPI backend, so we
// import the lower-level `ReconnectingWebSocket` from `partysocket/ws`.
import ReconnectingWebSocket from "partysocket/ws"

import { env } from "@/lib/env"

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
  return new ReconnectingWebSocket(url)
}
