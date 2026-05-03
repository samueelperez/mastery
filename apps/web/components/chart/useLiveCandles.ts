"use client"

import { useQuery } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"

import { fetchOhlcv, type CandleDTO } from "@/lib/api"
import { connectMarketWs, type KlinePayload, type MarketWsMessage } from "@/lib/ws"

export interface LiveCandle extends CandleDTO {
  isClosed?: boolean
}

export interface UseLiveCandlesResult {
  initial: LiveCandle[] | undefined
  loading: boolean
  error: Error | null
  /** The latest live candle (forming or just-closed). Updates from the WS feed. */
  live: LiveCandle | null
  /** Subscription status for the UI to surface a connection indicator. */
  wsConnected: boolean
}

/**
 * Loads N historical candles via REST, then keeps the most recent one in sync with the WS feed.
 * The chart component is responsible for diffing `live` into the on-screen series — this hook
 * deliberately does NOT mutate the historical array on every WS tick (avoids O(N) re-renders).
 */
export function useLiveCandles(symbol: string, timeframe: string, limit = 500): UseLiveCandlesResult {
  const query = useQuery({
    queryKey: ["ohlcv", symbol, timeframe, limit],
    queryFn: ({ signal }) => fetchOhlcv(symbol, timeframe, { limit, signal }),
  })

  const [live, setLive] = useState<LiveCandle | null>(null)
  const [wsConnected, setWsConnected] = useState(false)
  // Holds the PartySocket so cleanup on unmount is reliable.
  const wsRef = useRef<ReturnType<typeof connectMarketWs> | null>(null)

  useEffect(() => {
    const ws = connectMarketWs(symbol, timeframe)
    wsRef.current = ws

    const onOpen = () => setWsConnected(true)
    const onClose = () => setWsConnected(false)
    const onMessage = (event: MessageEvent<string>) => {
      let msg: MarketWsMessage
      try {
        msg = JSON.parse(event.data) as MarketWsMessage
      } catch {
        return
      }
      if (msg.type === "kline") {
        const payload: KlinePayload = msg.data
        setLive({
          ts: payload.ts,
          o: payload.o,
          h: payload.h,
          l: payload.l,
          c: payload.c,
          v: payload.v,
          isClosed: payload.is_closed,
        })
      }
    }

    ws.addEventListener("open", onOpen)
    ws.addEventListener("close", onClose)
    ws.addEventListener("message", onMessage)

    return () => {
      ws.removeEventListener("open", onOpen)
      ws.removeEventListener("close", onClose)
      ws.removeEventListener("message", onMessage)
      ws.close()
      wsRef.current = null
    }
  }, [symbol, timeframe])

  return {
    initial: query.data?.candles,
    loading: query.isLoading,
    error: query.error as Error | null,
    live,
    wsConnected,
  }
}
