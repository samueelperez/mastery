"use client"

import { useEffect, useRef, useState } from "react"

import { type AlertEventPayload, type AlertWsMessage, connectAlertsWs } from "@/lib/ws"

export interface UseAlertStreamResult {
  /** Most-recent N events received from the WS this session (newest first). */
  events: AlertEventPayload[]
  /** WS connection status — for the navbar bell pulse. */
  connected: boolean
}

/**
 * Single global subscription to /ws/alerts. Holds the last 50 events in
 * memory; older events fall off the tail. Reconnects automatically.
 *
 * NOT a substitute for the REST `/alerts/events` history — components that
 * need the full feed should fetch via react-query and merge the WS push on top.
 */
export function useAlertStream(userId: string = "me", buffer = 50): UseAlertStreamResult {
  const [events, setEvents] = useState<AlertEventPayload[]>([])
  const [connected, setConnected] = useState(false)
  const wsRef = useRef<ReturnType<typeof connectAlertsWs> | null>(null)

  useEffect(() => {
    const ws = connectAlertsWs(userId)
    wsRef.current = ws

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
      setEvents((prev) => [parsed!.data, ...prev].slice(0, buffer))
    }

    ws.addEventListener("open", onOpen)
    ws.addEventListener("close", onClose)
    ws.addEventListener("message", onMessage as EventListener)

    return () => {
      ws.removeEventListener("open", onOpen)
      ws.removeEventListener("close", onClose)
      ws.removeEventListener("message", onMessage as EventListener)
      ws.close()
      wsRef.current = null
    }
  }, [userId, buffer])

  return { events, connected }
}
