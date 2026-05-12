"use client"

import { useQuery } from "@tanstack/react-query"
import { BellIcon } from "lucide-react"
import { useMemo, useState } from "react"

import { AlertEventFeed } from "@/components/alerts/AlertEventFeed"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { useAlertStream } from "@/hooks/useAlertStream"
import { fetchAlertEvents } from "@/lib/core/api"
import { cn } from "@/lib/core/utils"

export function AlertBell() {
  const [open, setOpen] = useState(false)
  const { events: liveEvents } = useAlertStream("me")

  // Persisted unread (server-truth) — used for the badge count when the
  // popover is closed. WS pushes update the count in real-time via React Query
  // refetching when liveEvents change.
  const { data: unread = [] } = useQuery({
    queryKey: ["alert-events", { only_unread: true }],
    queryFn: ({ signal }) =>
      fetchAlertEvents({ only_unread: true, limit: 50, signal }),
    refetchInterval: 60_000,
  })

  // Merge live + persisted unread for the badge count: any live event not yet
  // marked seen is unread. Dedupe by id.
  const unreadCount = useMemo(() => {
    const ids = new Set<number>(unread.map((e) => e.id))
    for (const e of liveEvents) ids.add(e.event_id)
    return ids.size
  }, [unread, liveEvents])

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={
            unreadCount > 0
              ? `${unreadCount} alerta${unreadCount === 1 ? "" : "s"} sin leer`
              : "alertas"
          }
          className={cn(
            "relative grid size-9 place-items-center rounded-md transition-colors duration-150",
            "text-[var(--fg-2)] hover:bg-[var(--bg-2)] hover:text-foreground",
            "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2",
          )}
        >
          <BellIcon className="size-4" aria-hidden />
          {unreadCount > 0 && (
            <span
              className={cn(
                "absolute -right-0.5 -top-0.5 inline-flex h-[16px] min-w-[16px] items-center justify-center rounded-md px-1 text-[10px] font-bold tabular-nums",
                "bg-[var(--amber)] text-[var(--bg-0)]",
              )}
            >
              {unreadCount > 99 ? "99+" : unreadCount}
            </span>
          )}
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" sideOffset={8} className="p-0">
        <AlertEventFeed
          liveEvents={liveEvents}
          onClose={() => setOpen(false)}
        />
      </PopoverContent>
    </Popover>
  )
}
