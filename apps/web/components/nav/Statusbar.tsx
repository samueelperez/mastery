"use client"

import { useQuery } from "@tanstack/react-query"
import { usePathname } from "next/navigation"
import { useEffect, useState } from "react"

import { fetchHealth } from "@/lib/api"
import { cn } from "@/lib/utils"

/** Statusbar 22px sticky-bottom — sólo se renderiza fuera de /auth.
 *
 * Segmentos:
 *  - stream binance · Xms (verde si /health 2xx, mide RTT inline)
 *  - 1 símbolo (BTCUSDT por ahora; multi-symbol llega en F-multi)
 *  - copilot · sonnet 4.6 · 14 tools
 *  - vN · UTC offset · clock live
 */
export function Statusbar() {
  const pathname = usePathname()
  const hideStatusbar = pathname?.startsWith("/auth") ?? false

  const { data: probe } = useQuery({
    enabled: !hideStatusbar,
    queryKey: ["health-probe"],
    queryFn: async ({ signal }) => {
      const t0 = performance.now()
      try {
        const h = await fetchHealth({ signal })
        return { ok: h.status === "ok", ms: Math.round(performance.now() - t0) }
      } catch {
        return { ok: false, ms: null as number | null }
      }
    },
    refetchInterval: 30_000,
    staleTime: 15_000,
  })
  const ok = probe?.ok ?? false
  const ms = probe?.ms ?? null

  if (hideStatusbar) return null

  return (
    <footer
      role="contentinfo"
      className={cn(
        "flex h-[22px] shrink-0 items-center gap-4 border-t border-border bg-card px-3",
        "text-[10.5px] text-[var(--fg-3)]",
        "font-mono tracking-wide",
      )}
    >
      <span className="inline-flex items-center gap-1.5">
        <span
          className={cn("dot", ok ? "dot-live" : "dot-short")}
          aria-hidden
        />
        <span className={cn(ok && "text-[var(--long)]")}>stream binance</span>
        <span className="text-[var(--fg-4)]">·</span>
        <span className="tabular text-[var(--fg-2)]">
          {ms !== null ? `${ms}ms` : "—"}
        </span>
      </span>
      <span className="hidden items-center gap-1.5 sm:inline-flex">
        <span className="text-[var(--fg-4)]">·</span>
        <span>1 símbolo</span>
      </span>
      <span className="hidden items-center gap-1.5 md:inline-flex">
        <span className="text-[var(--fg-4)]">·</span>
        <span>copilot</span>
        <span className="text-[var(--fg-4)]">·</span>
        <span>sonnet 4.6</span>
        <span className="text-[var(--fg-4)]">·</span>
        <span className="tabular">14 tools</span>
      </span>
      <span className="ml-auto inline-flex items-center gap-3">
        <span className="hidden tabular sm:inline">v0.5.0</span>
        <ClockUtc />
      </span>
    </footer>
  )
}

function ClockUtc() {
  const [now, setNow] = useState<Date | null>(null)
  useEffect(() => {
    setNow(new Date())
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])
  if (!now) {
    return <span className="tabular text-[var(--fg-3)]">--:--:-- UTC</span>
  }
  const hh = String(now.getUTCHours()).padStart(2, "0")
  const mm = String(now.getUTCMinutes()).padStart(2, "0")
  const ss = String(now.getUTCSeconds()).padStart(2, "0")
  return (
    <span className="tabular text-[var(--fg-2)]">
      {hh}:{mm}:{ss} <span className="text-[var(--fg-4)]">UTC</span>
    </span>
  )
}
