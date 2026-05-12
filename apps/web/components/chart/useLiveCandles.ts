"use client"

import { useQuery } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"

import { fetchOhlcv, type CandleDTO } from "@/lib/core/api"
import { connectMarketWs, type KlinePayload, type MarketWsMessage } from "@/lib/core/ws"

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
  // Marca cuándo se cerró la conexión por última vez. Si el blackout supera
  // un candle period, al reconectar pedimos OHLCV fresco para incorporar
  // las velas que el backend acaba de rellenar via `_fill_gap`.
  const disconnectedAtRef = useRef<number | null>(null)

  useEffect(() => {
    // Al cambiar (symbol, tf) reseteamos `live`. Si no, la última tick del
    // símbolo previo se quedaría visible en el chart hasta que llegue la
    // primera tick del nuevo — que para 4h o 1d puede tardar minutos.
    setLive(null)
    setWsConnected(false)
    disconnectedAtRef.current = null

    const ws = connectMarketWs(symbol, timeframe)
    wsRef.current = ws

    const onOpen = () => {
      setWsConnected(true)
      // Si veníamos de un disconnect que duró más que una vela, el backend
      // pudo haber rellenado el hueco — refetch para traer esas velas al chart.
      const since = disconnectedAtRef.current
      disconnectedAtRef.current = null
      if (since !== null) {
        const blackoutMs = Date.now() - since
        if (blackoutMs >= timeframeMs(timeframe)) {
          void query.refetch()
        }
      }
    }
    const onClose = () => {
      setWsConnected(false)
      if (disconnectedAtRef.current === null) {
        disconnectedAtRef.current = Date.now()
      }
    }
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
    // query.refetch es estable referencialmente vía react-query; no incluirla
    // evita re-suscribir el WS en cada render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, timeframe])

  return {
    initial: query.data?.candles,
    loading: query.isLoading,
    error: query.error as Error | null,
    live,
    wsConnected,
  }
}

/** Duración nominal de un timeframe en ms. Usado para decidir si un blackout
 *  de WS fue suficientemente largo como para que el backend haya rellenado
 *  velas que el chart aún no conoce. */
function timeframeMs(tf: string): number {
  const m = /^(\d+)([mhd])$/.exec(tf)
  if (!m) return 60_000
  const n = Number(m[1])
  const unit = m[2]
  if (unit === "m") return n * 60_000
  if (unit === "h") return n * 60 * 60_000
  return n * 24 * 60 * 60_000
}
