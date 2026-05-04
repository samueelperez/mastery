"use client"

import { useQuery } from "@tanstack/react-query"

import { fetchOhlcv } from "@/lib/api"
import { formatTimeAgo } from "@/lib/format"
import { cn } from "@/lib/utils"

/** Tiny live-data strip rendered under the login card.
 *
 * One REST call to /ohlcv?limit=1 (already public), refetched every 30s.
 * No WebSocket — the auth page is a transient surface; opening a WS for
 * 5-10s of presence is wasteful. The point is to show the user this is a
 * live tool they're entering, not a marketing landing.
 */
export function LivePulse() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["auth-pulse-btcusdt-1h"],
    queryFn: ({ signal }) =>
      fetchOhlcv("BTCUSDT", "1h", { limit: 1, signal }),
    refetchInterval: 30_000,
    staleTime: 15_000,
  })

  const candle = data?.candles?.[0]
  const price = candle?.c
  const ts = candle?.ts

  return (
    <div
      role="status"
      aria-live="polite"
      className="flex flex-wrap items-center justify-center gap-x-3 gap-y-1 font-mono text-[11px] uppercase tracking-widest text-muted-foreground"
    >
      <span className="flex items-center gap-1.5">
        <span
          className={cn(
            "size-1.5 rounded-full transition-colors duration-300",
            isError
              ? "bg-destructive"
              : isLoading || !candle
                ? "bg-muted-foreground/40 animate-pulse"
                : "bg-success",
          )}
          aria-hidden
        />
        <span className="text-foreground tabular-nums">
          {price !== undefined ? `$${price.toLocaleString("en-US")}` : "—"}
        </span>
        <span>BTC</span>
      </span>
      <span aria-hidden>·</span>
      <span className="tabular-nums">
        {ts ? `last bar ${formatTimeAgo(ts)}` : "syncing"}
      </span>
      <span aria-hidden>·</span>
      <span>14 tools</span>
      <span aria-hidden>·</span>
      <span>88 tests</span>
    </div>
  )
}
