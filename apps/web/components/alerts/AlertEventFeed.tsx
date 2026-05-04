"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { AlertTriangleIcon, BellIcon, CheckIcon } from "lucide-react"
import { useMemo } from "react"

import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import { formatTimeAgo } from "@/lib/format"
import { cn } from "@/lib/utils"
import {
  type AlertEventDTO,
  fetchAlertEvents,
  markEventSeen,
} from "@/lib/api"
import { type AlertEventPayload } from "@/lib/ws"

interface AlertEventFeedProps {
  liveEvents: AlertEventPayload[]
  onClose?: () => void
}

function severityClass(sev: string): string {
  if (sev === "high") return "text-destructive"
  if (sev === "medium") return "text-warning"
  return "text-muted-foreground"
}

export function AlertEventFeed({ liveEvents, onClose }: AlertEventFeedProps) {
  const qc = useQueryClient()
  const { data: persisted = [], isLoading } = useQuery({
    queryKey: ["alert-events", { limit: 30 }],
    queryFn: ({ signal }) => fetchAlertEvents({ limit: 30, signal }),
  })
  const markSeen = useMutation({
    mutationFn: (id: number) => markEventSeen(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alert-events"] }),
  })

  // Merge: live events (newest, may not be in REST yet) + persisted (full
  // history). Dedupe on event_id; live wins.
  const merged = useMemo(() => {
    const seen = new Set<number>()
    const all: AlertEventDTO[] = []
    for (const ev of liveEvents) {
      if (seen.has(ev.event_id)) continue
      seen.add(ev.event_id)
      all.push({
        id: ev.event_id,
        rule_id: ev.rule_id,
        kind: ev.kind,
        severity: ev.severity,
        fired_at: ev.fired_at,
        snapshot: ev.snapshot,
        seen_at: null,
      })
    }
    for (const ev of persisted) {
      if (seen.has(ev.id)) continue
      seen.add(ev.id)
      all.push(ev)
    }
    return all
  }, [liveEvents, persisted])

  return (
    <div className="flex w-80 max-w-[90vw] flex-col">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <span className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
          alertas
        </span>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            className="rounded text-muted-foreground hover:text-foreground"
            aria-label="cerrar"
          >
            ×
          </button>
        )}
      </div>
      <ScrollArea className="max-h-96">
        {isLoading && merged.length === 0 ? (
          <p className="px-3 py-6 text-center text-xs text-muted-foreground">
            cargando…
          </p>
        ) : merged.length === 0 ? (
          <div className="flex flex-col items-center gap-2 px-3 py-8 text-center">
            <BellIcon className="size-5 text-muted-foreground" aria-hidden />
            <p className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
              todo en orden
            </p>
            <p className="text-xs text-muted-foreground">
              aún no hay eventos — las alertas que crees disparan aquí
            </p>
          </div>
        ) : (
          <ul className="divide-y divide-border/30">
            {merged.map((ev) => {
              const unread = ev.seen_at === null
              const ruleName =
                (ev.snapshot as { rule_name?: string }).rule_name ??
                (ev.kind === "bias_promoted" ? "bias_event" : "rule_match")
              return (
                <li
                  key={ev.id}
                  className={cn(
                    "flex flex-col gap-1 px-3 py-2 transition-colors",
                    unread && "bg-accent/10",
                  )}
                >
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="font-mono text-xs text-foreground">
                      {ruleName}
                    </span>
                    <span className="font-mono text-[10px] tabular-nums text-muted-foreground">
                      {formatTimeAgo(ev.fired_at)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between gap-2">
                    <span className="flex items-center gap-1 font-mono text-[11px]">
                      <AlertTriangleIcon
                        className={cn("size-3", severityClass(ev.severity))}
                        aria-hidden
                      />
                      <Badge
                        variant="outline"
                        className={cn("text-[10px]", severityClass(ev.severity))}
                      >
                        {ev.kind === "rule_match" ? "regla" : "sesgo"} ·{" "}
                        {ev.severity}
                      </Badge>
                    </span>
                    {unread && (
                      <button
                        type="button"
                        onClick={() => markSeen.mutate(ev.id)}
                        className="flex items-center gap-1 font-mono text-[10px] uppercase tracking-widest text-muted-foreground transition-colors hover:text-foreground"
                      >
                        <CheckIcon className="size-3" aria-hidden />
                        marcar leído
                      </button>
                    )}
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </ScrollArea>
    </div>
  )
}
