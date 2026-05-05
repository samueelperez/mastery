"use client"

import { useQuery } from "@tanstack/react-query"

import { cn } from "@/lib/utils"
import { fetchHealth } from "@/lib/api"

/** Live data-plane indicator for the global nav — `.pill` style.
 *
 * Polls `/health` every 30s. Three visual states:
 *   - long  (green) — db & valkey both ok
 *   - amber         — partial: api up but one dependency down
 *   - short (red)   — api unreachable / non-2xx
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
    const allOk =
      data.db === "ok" &&
      data.valkey === "ok" &&
      data.openrouter === "configured" &&
      data.voyage === "configured"
    if (allOk) return "ok"
    return "partial"
  })()

  const dotClass = {
    loading: "bg-[var(--fg-3)] animate-pulse",
    ok: "dot-live",
    partial: "dot-amber",
    down: "dot-short",
  }[tone]

  const title = {
    loading: "verificando data plane…",
    ok: "data plane saludable: db + valkey + openrouter + voyage configurados",
    partial: data
      ? `degradado: db=${data.db} valkey=${data.valkey} openrouter=${data.openrouter} voyage=${data.voyage}`
      : "degradado",
    down: "api inaccesible",
  }[tone]

  return (
    <span title={title} className="pill hidden md:inline-flex">
      <span className={cn("dot", dotClass)} aria-hidden />
      <span>binance · usdt-m</span>
    </span>
  )
}
