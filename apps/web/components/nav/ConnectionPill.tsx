"use client"

import { useQuery } from "@tanstack/react-query"

import { cn } from "@/lib/utils"
import { fetchHealth } from "@/lib/api"

/** Live data-plane indicator for the global nav.
 *
 * Polls `/health` every 30s. Three visual states:
 *   - green  (primary)    — db & valkey both ok
 *   - amber                — partial: api up but one dependency down
 *   - red    (destructive) — api unreachable / non-2xx
 *
 * Click target is decorative; status is read-only.
 */
export function ConnectionPill() {
  const { data, isError, isLoading } = useQuery({
    queryKey: ["health"],
    queryFn: ({ signal }) => fetchHealth({ signal }),
    refetchInterval: 30_000,
    staleTime: 15_000,
    retry: 1,
  })

  const tone = (() => {
    if (isLoading) return "loading"
    if (isError || !data) return "down"
    if (data.db === "ok" && data.valkey === "ok") return "ok"
    return "partial"
  })()

  const dotClass = {
    loading: "bg-muted-foreground animate-pulse",
    ok: "bg-primary",
    partial: "bg-amber-400",
    down: "bg-destructive",
  }[tone]

  const title = {
    loading: "checking data plane…",
    ok: "data plane healthy: db + valkey reachable",
    partial: data
      ? `partial: db=${data.db} · valkey=${data.valkey}`
      : "partial",
    down: "api unreachable",
  }[tone]

  return (
    <span
      title={title}
      className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-widest text-muted-foreground"
    >
      <span className={cn("size-1.5 rounded-full", dotClass)} aria-hidden />
      <span className="hidden sm:inline">Binance USDT-M · MAINNET-RO</span>
      <span className="sm:hidden">live</span>
    </span>
  )
}
