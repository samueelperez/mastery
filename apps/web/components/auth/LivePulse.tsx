"use client"

import { useQuery } from "@tanstack/react-query"
import { ActivityIcon, FlaskConicalIcon, BellRingIcon } from "lucide-react"

import { fetchOhlcv } from "@/lib/api"
import { formatTimeAgo } from "@/lib/format"
import { cn } from "@/lib/utils"

interface LiveRowProps {
  icon: React.ReactNode
  label: string
  value: string
  hint?: string
  status?: "live" | "ok" | "loading"
}

function LiveRow({ icon, label, value, hint, status = "ok" }: LiveRowProps) {
  return (
    <div className="flex items-center gap-3 rounded-md border border-border/40 bg-card/40 px-3 py-2 backdrop-blur-sm">
      <span
        className={cn(
          "grid size-6 place-items-center rounded border border-border/50 bg-[var(--bg-2)]/50",
          "text-[var(--amber)]",
        )}
        aria-hidden
      >
        {icon}
      </span>
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--fg-3)]">
          {label}
        </span>
        <span className="truncate font-mono text-[12px] tabular-nums text-foreground">
          {value}
        </span>
      </div>
      {hint && (
        <span className="hidden font-mono text-[10px] tabular-nums text-[var(--fg-3)] sm:inline">
          {hint}
        </span>
      )}
      <span
        className={cn(
          "dot ml-1",
          status === "live"
            ? "dot-live"
            : status === "loading"
              ? "bg-[var(--fg-3)] animate-pulse"
              : "dot-amber",
        )}
        aria-hidden
      />
    </div>
  )
}

/** Tres filas live debajo del pitch: precio BTC en vivo + 2 metadata
 *  estáticas (claims que mantenemos hasta tener counts dinámicos). */
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

  const priceText =
    price !== undefined
      ? `$${price.toLocaleString("en-US", { maximumFractionDigits: 2 })}`
      : isError
        ? "sin datos"
        : "—"

  return (
    <div className="flex flex-col gap-2" role="status" aria-live="polite">
      <LiveRow
        icon={<ActivityIcon className="size-3" />}
        label="Precio en vivo · BTC"
        value={priceText}
        hint={ts ? formatTimeAgo(ts) : undefined}
        status={isLoading ? "loading" : isError ? "ok" : "live"}
      />
      <LiveRow
        icon={<FlaskConicalIcon className="size-3" />}
        label="Estrategias validadas"
        value="Probamos con datos antes de operar"
        status="ok"
      />
      <LiveRow
        icon={<BellRingIcon className="size-3" />}
        label="Alertas inteligentes"
        value="Te avisamos cuando algo importante pasa"
        status="ok"
      />
    </div>
  )
}
