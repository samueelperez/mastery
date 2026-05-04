"use client"

import { useEffect, useState } from "react"

import { type AlertEventPayload, type AlertWsMessage, connectAlertsWs } from "@/lib/ws"

export interface UseAlertStreamResult {
  /** Most-recent N events received from the WS this session (newest first). */
  events: AlertEventPayload[]
  /** WS connection status — for the navbar bell pulse. */
  connected: boolean
}

/**
 * Single global subscription to /ws/alerts. Holds the last N events in
 * memory; older events fall off the tail. Reconnects automatically and
 * dedupes by event_id so a WS reconnect that re-delivers a recent event
 * doesn't double-count the badge.
 */
export function useAlertStream(userId: string = "me", buffer = 50): UseAlertStreamResult {
  const [events, setEvents] = useState<AlertEventPayload[]>([])
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    const ws = connectAlertsWs(userId)

    const onOpen = () => setConnected(true)
    const onClose = () => setConnected(false)
    const onMessage = (e: MessageEvent<string>) => {
      let parsed: AlertWsMessage | null = null
      try {
        parsed = JSON.parse(e.data) as AlertWsMessage
      } catch {
        return
      }
      if (parsed.type !== "alert_event") return
      const data = parsed.data
      setEvents((prev) => {
        if (prev.some((e) => e.event_id === data.event_id)) return prev
        return [data, ...prev].slice(0, buffer)
      })
    }

    ws.addEventListener("open", onOpen)
    ws.addEventListener("close", onClose)
    ws.addEventListener("message", onMessage as EventListener)

    return () => {
      ws.removeEventListener("open", onOpen)
      ws.removeEventListener("close", onClose)
      ws.removeEventListener("message", onMessage as EventListener)
      ws.close()
    }
  }, [userId, buffer])

  return { events, connected }
}
