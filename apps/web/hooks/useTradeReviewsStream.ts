"use client"

import { useEffect, useRef } from "react"

import {
  connectReviewsWs,
  type PostMortemPayload,
  type ReviewWsMessage,
  type TradeReviewPayload,
} from "@/lib/ws"

export interface UseTradeReviewsStreamResult {
  connected: boolean
}

/**
 * Subscribe to /ws/reviews and call `onReview` for each TradeReview pushed
 * by the backend, and `onPostMortem` for each terminal post-mortem (SL/TP
 * hit). One WS connection per mount, two discriminators on the same envelope.
 *
 * The callback identity is captured per-mount via a ref so we don't reconnect
 * every render.
 */
export function useTradeReviewsStream(
  onReview: (review: TradeReviewPayload) => void,
  onPostMortem?: (postMortem: PostMortemPayload) => void,
): UseTradeReviewsStreamResult {
  const reviewCbRef = useRef(onReview)
  const pmCbRef = useRef(onPostMortem)
  reviewCbRef.current = onReview
  pmCbRef.current = onPostMortem

  useEffect(() => {
    const ws = connectReviewsWs()

    const onMessage = (e: MessageEvent<string>) => {
      let parsed: ReviewWsMessage | null = null
      try {
        parsed = JSON.parse(e.data) as ReviewWsMessage
      } catch {
        return
      }
      if (parsed.type === "trade_review") {
        reviewCbRef.current(parsed.data)
      } else if (parsed.type === "post_mortem") {
        pmCbRef.current?.(parsed.data)
      }
    }
    ws.addEventListener("message", onMessage as EventListener)
    return () => {
      ws.removeEventListener("message", onMessage as EventListener)
      ws.close()
    }
  }, [])

  return { connected: false }
}
