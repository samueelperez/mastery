"use client"

import { ChevronDownIcon, TrendingUpIcon } from "lucide-react"

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type {
  MarketStructureDTO,
  ToolResultEnvelope,
} from "@/lib/agent-outputs"
import { cn } from "@/lib/utils"

interface StructureSummaryCardProps {
  output: ToolResultEnvelope<MarketStructureDTO> | MarketStructureDTO
  input: { symbol?: string; timeframe?: string }
}

const TREND_LABEL: Record<string, { short: string; long: string; dot: string }> = {
  HH_HL: {
    short: "tendencia alcista",
    long: "El precio hace máximos y mínimos cada vez más altos. Estructura típica de subida sostenida.",
    dot: "bg-[var(--long)]",
  },
  LH_LL: {
    short: "tendencia bajista",
    long: "El precio hace máximos y mínimos cada vez más bajos. Estructura típica de bajada sostenida.",
    dot: "bg-[var(--short)]",
  },
  mixed: {
    short: "estructura mixta",
    long: "Los puntos clave no coinciden con una dirección — posible rotación o consolidación.",
    dot: "bg-[var(--violet)]",
  },
  indeterminate: {
    short: "sin estructura clara",
    long: "Faltan puntos clave de referencia para identificar la estructura.",
    dot: "bg-[var(--fg-3)]",
  },
}

interface ProximityInfo {
  closestRes: { price: number; touches: number; distPct: number } | null
  closestSup: { price: number; touches: number; distPct: number } | null
}

function computeProximity(data: MarketStructureDTO): ProximityInfo {
  const close = data.current_close
  if (close === null || close <= 0) return { closestRes: null, closestSup: null }

  // Resistencia más cercana POR ENCIMA del close
  const resAbove = data.resistance
    .filter((r) => r.price > close)
    .sort((a, b) => a.price - b.price)[0]
  // Soporte más cercano POR DEBAJO del close
  const supBelow = data.support
    .filter((s) => s.price < close)
    .sort((a, b) => b.price - a.price)[0]

  return {
    closestRes: resAbove
      ? {
          price: resAbove.price,
          touches: resAbove.touches,
          distPct: ((resAbove.price - close) / close) * 100,
        }
      : null,
    closestSup: supBelow
      ? {
          price: supBelow.price,
          touches: supBelow.touches,
          distPct: ((close - supBelow.price) / close) * 100,
        }
      : null,
  }
}

/** Interpretación humana del structure en lenguaje accesible. */
function interpretStructure(
  data: MarketStructureDTO,
  prox: ProximityInfo,
): string {
  const trend = TREND_LABEL[data.trend_label] ?? TREND_LABEL.indeterminate!

  if (prox.closestRes && prox.closestSup) {
    return (
      `${trend.short} · resistencia a +${prox.closestRes.distPct.toFixed(2)}%, ` +
      `soporte a −${prox.closestSup.distPct.toFixed(2)}%`
    )
  }
  if (prox.closestRes) {
    return `${trend.short} · próxima resistencia un ${prox.closestRes.distPct.toFixed(2)}% arriba`
  }
  if (prox.closestSup) {
    return `${trend.short} · próximo soporte un ${prox.closestSup.distPct.toFixed(2)}% abajo`
  }
  if (data.swing_highs.length + data.swing_lows.length === 0) {
    return `${trend.short} · faltan puntos clave para analizar`
  }
  return trend.short
}

export function StructureSummaryCard({
  output,
  input,
}: StructureSummaryCardProps) {
  const data = "data" in output ? output.data : output
  const symbol = input.symbol?.toUpperCase() ?? "?"
  const tf = input.timeframe ?? "?"
  const trend = TREND_LABEL[data.trend_label] ?? TREND_LABEL.indeterminate!
  const prox = computeProximity(data)
  const interpretation = interpretStructure(data, prox)

  const closestN = <T extends { price: number }>(
    arr: T[],
    n: number,
    ref: number,
  ): T[] =>
    [...arr]
      .sort((a, b) => Math.abs(a.price - ref) - Math.abs(b.price - ref))
      .slice(0, n)
  const ref = data.current_close ?? 0
  const supports = closestN(data.support, 4, ref)
  const resistances = closestN(data.resistance, 4, ref)

  return (
    <Collapsible
      className={cn(
        "group/struct w-full min-w-0 overflow-hidden rounded-md border border-border bg-card",
      )}
    >
      <CollapsibleTrigger
        className={cn(
          "flex w-full items-start gap-2 px-3 py-2 text-left",
          "transition-colors hover:bg-[var(--bg-2)]/50",
          "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-[-2px]",
        )}
      >
        <TrendingUpIcon
          className="mt-0.5 size-3.5 shrink-0 text-[var(--violet)]"
          aria-hidden
        />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <div className="flex items-center gap-2">
            <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
              estructura · {symbol} {tf}
            </span>
            <span aria-hidden className={cn("dot ml-auto", trend.dot)} />
          </div>
          <Tooltip>
            <TooltipTrigger asChild>
              <p className="cursor-help truncate font-mono text-[12px] leading-snug text-foreground">
                {interpretation}
              </p>
            </TooltipTrigger>
            <TooltipContent>{trend.long}</TooltipContent>
          </Tooltip>
        </div>
        <ChevronDownIcon
          className="mt-1 size-3 shrink-0 text-[var(--fg-3)] transition-transform group-data-[state=open]/struct:rotate-180"
          aria-hidden
        />
      </CollapsibleTrigger>
      <CollapsibleContent className="border-t border-[color:var(--line-soft)] px-3 py-2">
        <div className="mb-2 flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[10px]">
          {data.current_close !== null && (
            <span>
              <span className="text-[var(--fg-3)]">close </span>
              <span className="tabular-nums text-foreground">
                {fmt(data.current_close)}
              </span>
            </span>
          )}
          {data.atr_used !== null && (
            <span>
              <span className="text-[var(--fg-3)]">ATR </span>
              <span className="tabular-nums text-foreground">
                {fmt(data.atr_used)}
              </span>
            </span>
          )}
          <span className="text-[var(--fg-3)]">
            {data.swing_highs.length}H · {data.swing_lows.length}L pivots
          </span>
        </div>
        <div className="grid min-w-0 grid-cols-2 gap-2">
          <LevelColumn
            title={`resistencias (${data.resistance.length})`}
            items={resistances}
            tone="short"
            currentPrice={data.current_close}
          />
          <LevelColumn
            title={`soportes (${data.support.length})`}
            items={supports}
            tone="long"
            currentPrice={data.current_close}
          />
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}

interface LevelColumnProps {
  title: string
  items: { price: number; touches: number }[]
  tone: "long" | "short"
  currentPrice: number | null
}

function LevelColumn({ title, items, tone, currentPrice }: LevelColumnProps) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span className="truncate font-mono text-[9px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
        {title}
      </span>
      <ul className="flex flex-col gap-0.5 font-mono text-[10.5px] tabular-nums">
        {items.length === 0 ? (
          <li className="text-[var(--fg-4)]">—</li>
        ) : (
          items.map((lvl, idx) => {
            const distancePct =
              currentPrice !== null && currentPrice > 0
                ? ((lvl.price - currentPrice) / currentPrice) * 100
                : null
            return (
              <li
                key={idx}
                className={cn(
                  "flex min-w-0 items-center justify-between gap-1 rounded px-1.5 py-0.5",
                  tone === "long"
                    ? "bg-[var(--long-bg)] text-[var(--long)]"
                    : "bg-[var(--short-bg)] text-[var(--short)]",
                )}
              >
                <span className="truncate">{fmt(lvl.price)}</span>
                <span className="flex shrink-0 items-baseline gap-1 text-[9px]">
                  {distancePct !== null && (
                    <span className="opacity-60">
                      {distancePct >= 0 ? "+" : ""}
                      {distancePct.toFixed(1)}%
                    </span>
                  )}
                  <span className="opacity-70">{lvl.touches}x</span>
                </span>
              </li>
            )
          })
        )}
      </ul>
    </div>
  )
}

function fmt(v: number): string {
  return v.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

export function isMarketStructureOutput(
  value: unknown,
): value is ToolResultEnvelope<MarketStructureDTO> | MarketStructureDTO {
  if (!value || typeof value !== "object") return false
  const v = value as Record<string, unknown>
  const data = "data" in v ? (v.data as Record<string, unknown>) : v
  if (typeof data !== "object" || data === null) return false
  const d = data as Record<string, unknown>
  return (
    Array.isArray(d.swing_highs) &&
    Array.isArray(d.swing_lows) &&
    typeof d.trend_label === "string"
  )
}
