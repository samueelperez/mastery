"use client"

import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react"

import type { CandleDTO } from "@/lib/core/api"
import type {
  OverlayBundle,
  TradeIdeaOverlay,
} from "@/lib/store/chart-overlays"
import { cn } from "@/lib/core/utils"

import {
  computeEma,
  computeSma,
} from "./overlays/computeIndicators"

interface ChartLegendProps {
  overlays: OverlayBundle | null
  initial: CandleDTO[] | undefined
  activeTimeframe?: string
  minimalMode?: boolean
  ideas?: TradeIdeaOverlay[]
  activeIdeaId?: string | null
  onSelectIdea?: (id: string) => void
}

/** Leyenda flotante TradingView-style sobre el chart.
 *
 *  Anclada `top-2 left-2 absolute` dentro del container relative del chart.
 *  Sólo se renderiza si hay capas activas. La fila del trade idea es
 *  interactiva cuando `ideas.length > 1` (switcher con flechas); el resto
 *  de filas no interactivas (`pointer-events-none` en el wrapper, override
 *  con `pointer-events-auto` en los botones del switcher).
 */
export function ChartLegend({
  overlays,
  initial,
  activeTimeframe,
  minimalMode = false,
  ideas = [],
  activeIdeaId = null,
  onSelectIdea,
}: ChartLegendProps) {
  const ind = overlays?.indicators
  const structure = overlays?.structure

  const indicatorRows: LegendRow[] = []

  // EMAs ordenadas
  if (ind && initial && initial.length > 0) {
    for (const period of ind.ema) {
      const last = lastValueOf(computeEma(initial, period))
      indicatorRows.push({
        key: `ema-${period}`,
        dotClass: emaDot(period),
        label: `EMA ${period}`,
        value: last !== null ? formatPrice(last) : "—",
      })
    }
    for (const period of ind.sma) {
      const last = lastValueOf(computeSma(initial, period))
      indicatorRows.push({
        key: `sma-${period}`,
        dotClass: smaDot(period),
        label: `SMA ${period}`,
        value: last !== null ? formatPrice(last) : "—",
      })
    }
    if (ind.bbands) {
      indicatorRows.push({
        key: "bb",
        dotClass: "bg-[var(--fg-4)]",
        label: "BB 20",
        value: "20 / 2σ",
      })
    }
    if (ind.vwap) {
      indicatorRows.push({
        key: "vwap",
        dotClass: "bg-[var(--long)]",
        label: "VWAP",
        value: "sesión",
      })
    }
  }

  // Trade idea activo (puede haber switcher si ideas.length > 1).
  const activeIdx = ideas.findIndex((i) => i.id === activeIdeaId)
  const tradeIdea = activeIdx >= 0 ? ideas[activeIdx]! : ideas[0] ?? null
  const showSwitcher = ideas.length > 1 && tradeIdea !== null

  // Structure resumen (sólo si NO está en minimal mode y hay structure)
  const structureRow: LegendRow | null = (() => {
    if (!structure || minimalMode) return null
    const total = structure.support.length + structure.resistance.length
    const tfLabel =
      activeTimeframe && activeTimeframe === structure.tf
        ? structure.tf
        : `${structure.tf} (otro tf)`
    return {
      key: "structure",
      dotClass: "bg-[var(--violet)]",
      label: "Structure",
      value: `${total} S/R · ${tfLabel}`,
    }
  })()

  if (
    indicatorRows.length === 0 &&
    !tradeIdea &&
    !structureRow
  ) {
    return null
  }

  return (
    <div
      role="status"
      aria-label="capas activas en el chart"
      className={cn(
        "pointer-events-none absolute left-2 top-2 z-10",
        "flex flex-col gap-0.5 rounded-md border border-border",
        "bg-card/85 backdrop-blur-sm",
        "px-2 py-1.5 font-mono text-[10px] leading-tight tracking-tight",
      )}
    >
      {indicatorRows.map((row) => (
        <Row key={row.key} row={row} />
      ))}
      {tradeIdea && (
        <TradeIdeaRow
          idea={tradeIdea}
          showSwitcher={showSwitcher}
          activeIdx={activeIdx >= 0 ? activeIdx : 0}
          total={ideas.length}
          onPrev={() => {
            if (!onSelectIdea || ideas.length === 0) return
            const cur = activeIdx >= 0 ? activeIdx : 0
            const prev = (cur - 1 + ideas.length) % ideas.length
            onSelectIdea(ideas[prev]!.id)
          }}
          onNext={() => {
            if (!onSelectIdea || ideas.length === 0) return
            const cur = activeIdx >= 0 ? activeIdx : 0
            const next = (cur + 1) % ideas.length
            onSelectIdea(ideas[next]!.id)
          }}
        />
      )}
      {structureRow && <Row row={structureRow} />}
    </div>
  )
}

interface LegendRow {
  key: string
  dotClass: string
  label: string
  value: string
}

function Row({ row }: { row: LegendRow }) {
  return (
    <div className="flex items-center gap-1.5">
      <span aria-hidden className={cn("dot size-1.5", row.dotClass)} />
      <span className="text-[var(--fg-2)]">{row.label}</span>
      <span className="ml-auto pl-3 tabular text-foreground">{row.value}</span>
    </div>
  )
}

function TradeIdeaRow({
  idea,
  showSwitcher,
  activeIdx,
  total,
  onPrev,
  onNext,
}: {
  idea: TradeIdeaOverlay
  showSwitcher: boolean
  activeIdx: number
  total: number
  onPrev: () => void
  onNext: () => void
}) {
  const r = computeRMultiple(idea)
  const directionLabel = idea.direction === "long" ? "Long" : "Short"
  const dotClass =
    idea.direction === "long" ? "bg-[var(--long)]" : "bg-[var(--short)]"
  const valueText = `${formatPrice(idea.entry)} → SL ${formatPrice(idea.stopLoss)}${r ? ` · ${r}` : ""}`

  if (!showSwitcher) {
    return (
      <Row
        row={{
          key: "trade",
          dotClass,
          label: directionLabel,
          value: valueText,
        }}
      />
    )
  }

  return (
    <div className="flex items-center gap-1.5">
      <span aria-hidden className={cn("dot size-1.5", dotClass)} />
      <button
        type="button"
        onClick={onPrev}
        aria-label="setup anterior"
        className={cn(
          "pointer-events-auto inline-flex size-3.5 items-center justify-center rounded-sm",
          "text-[var(--fg-3)] transition-colors hover:bg-[var(--bg-2)] hover:text-foreground",
          "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-1",
        )}
      >
        <ChevronLeftIcon className="size-3" aria-hidden />
      </button>
      <span className="tabular-nums text-[var(--fg-2)]">
        {directionLabel} {activeIdx + 1}/{total}
      </span>
      <button
        type="button"
        onClick={onNext}
        aria-label="setup siguiente"
        className={cn(
          "pointer-events-auto inline-flex size-3.5 items-center justify-center rounded-sm",
          "text-[var(--fg-3)] transition-colors hover:bg-[var(--bg-2)] hover:text-foreground",
          "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-1",
        )}
      >
        <ChevronRightIcon className="size-3" aria-hidden />
      </button>
      <span className="ml-auto pl-3 tabular text-foreground">{valueText}</span>
    </div>
  )
}

function emaDot(period: number): string {
  if (period === 21) return "bg-[var(--amber)]"
  if (period === 55) return "bg-[var(--violet)]"
  if (period === 200) return "bg-[var(--fg-3)]"
  return "bg-[var(--fg-2)]"
}

function smaDot(period: number): string {
  if (period === 50) return "bg-[var(--long)]"
  if (period === 100) return "bg-[var(--violet)]"
  return "bg-[var(--fg-2)]"
}

function lastValueOf(arr: { value: number }[]): number | null {
  if (arr.length === 0) return null
  return arr[arr.length - 1]!.value
}

function formatPrice(p: number): string {
  return p.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

/** R-multiple del primer target. Si no se puede calcular, devuelve null.
 *  Math: |TP1 - entry| / |entry - SL|, signed para SHORT también. */
function computeRMultiple(idea: TradeIdeaOverlay): string | null {
  if (idea.targets.length === 0) return null
  const risk = Math.abs(idea.entry - idea.stopLoss)
  if (risk === 0) return null
  const tp = idea.targets[0]!.price
  const reward = idea.direction === "long" ? tp - idea.entry : idea.entry - tp
  const r = reward / risk
  if (!Number.isFinite(r)) return null
  const sign = r >= 0 ? "+" : ""
  return `${sign}${r.toFixed(1)}R`
}
