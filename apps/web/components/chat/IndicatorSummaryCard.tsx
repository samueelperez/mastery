"use client"

import { ActivityIcon, ChevronDownIcon, InfoIcon } from "lucide-react"

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
  IndicatorPanelDTO,
  ToolResultEnvelope,
} from "@/lib/agent/outputs"
import { cn } from "@/lib/core/utils"

interface IndicatorSummaryCardProps {
  output: ToolResultEnvelope<IndicatorPanelDTO> | IndicatorPanelDTO
  input: { symbol?: string; timeframe?: string }
}

type Category = "trend" | "momentum" | "volatility" | "volume" | "other"

interface IndicatorMeta {
  category: Category
  description: string
}

const INDICATOR_META: Record<string, IndicatorMeta> = {
  ema: { category: "trend", description: "Media móvil exponencial — pondera más las velas recientes." },
  sma: { category: "trend", description: "Media móvil simple — promedio sin ponderación." },
  vwap: { category: "trend", description: "Volume-Weighted Average Price — soporte intradía clave." },
  rsi: { category: "momentum", description: "Relative Strength Index 0-100. >70 sobrecompra, <30 sobreventa." },
  macd: { category: "momentum", description: "MACD: diferencia entre EMA12 y EMA26." },
  macd_signal: { category: "momentum", description: "Signal line: EMA9 del MACD. Cruce = señal." },
  macd_hist: { category: "momentum", description: "Histograma: MACD - Signal. Mide aceleración." },
  adx: { category: "momentum", description: "Average Directional Index 0-100. >25 trend fuerte." },
  plus_di: { category: "momentum", description: "+DI: presión compradora del ADX." },
  minus_di: { category: "momentum", description: "-DI: presión vendedora del ADX." },
  atr: { category: "volatility", description: "Average True Range — magnitud media del rango por vela." },
  bb_mid: { category: "volatility", description: "Bollinger middle = SMA(20)." },
  bb_upper: { category: "volatility", description: "Bollinger upper = SMA + 2σ." },
  bb_lower: { category: "volatility", description: "Bollinger lower = SMA - 2σ." },
  bb_bw: { category: "volatility", description: "Bandwidth — ancho relativo de las bandas (squeeze indicator)." },
}

const CATEGORY_LABEL: Record<Category, string> = {
  trend: "Tendencia",
  momentum: "Momentum",
  volatility: "Volatilidad",
  volume: "Volumen",
  other: "Otros",
}

const CATEGORY_ORDER: Category[] = ["trend", "momentum", "volatility", "volume", "other"]

interface IndicatorRow {
  key: string
  prettyName: string
  metaKey: string
  value: number | null
}

interface FlattenedValues {
  rows: IndicatorRow[]
  /** Lookup directo por key normalizada para la interpretación. */
  byKey: Record<string, number>
}

function flattenLatest(latest: Record<string, unknown>): FlattenedValues {
  const rows: IndicatorRow[] = []
  const byKey: Record<string, number> = {}
  for (const [k, v] of Object.entries(latest)) {
    if (v === null || v === undefined) continue
    if (typeof v === "number") {
      rows.push({ key: k, prettyName: k.replace(/_/g, " "), metaKey: stripLength(k), value: v })
      byKey[k] = v
      continue
    }
    if (typeof v === "object") {
      for (const [subK, subV] of Object.entries(v as Record<string, unknown>)) {
        if (typeof subV === "number") {
          rows.push({
            key: subK,
            prettyName: subK.replace(/_/g, " "),
            metaKey: stripLength(subK),
            value: subV,
          })
          byKey[subK] = subV
        }
      }
    }
  }
  return { rows, byKey }
}

function stripLength(key: string): string {
  const m = key.match(/^([a-z]+(?:_[a-z]+)*)_(\d+)$/)
  if (m && m[1]) return m[1]
  return key
}

/** Interpretación humana de los indicadores en lenguaje accesible para
 *  trader principiante/medio. Evita jerga cuando puede; cuando la usa, la
 *  acompaña con la idea (ej. "RSI 75 — zona de sobrecompra, posible techo"). */
function interpretIndicators(byKey: Record<string, number>): string {
  const parts: string[] = []

  // EMA stack: ema_21 vs ema_55 vs ema_200
  const ema21 = byKey.ema_21
  const ema55 = byKey.ema_55
  const ema200 = byKey.ema_200
  if (ema21 !== undefined && ema55 !== undefined && ema200 !== undefined) {
    if (ema21 > ema55 && ema55 > ema200)
      parts.push("Medias móviles alineadas al alza")
    else if (ema21 < ema55 && ema55 < ema200)
      parts.push("Medias móviles alineadas a la baja")
    else parts.push("Medias móviles cruzadas (sin dirección clara)")
  } else if (ema21 !== undefined && ema55 !== undefined) {
    parts.push(
      ema21 > ema55
        ? "Media corta sobre la larga (favorable al alza)"
        : "Media corta bajo la larga (favorable a la baja)",
    )
  }

  // RSI: explicación accesible de los niveles
  const rsi = byKey.rsi_14
  if (rsi !== undefined) {
    if (rsi >= 70)
      parts.push(`RSI ${rsi.toFixed(0)} en sobrecompra (cuidado con techo)`)
    else if (rsi >= 60) parts.push(`RSI ${rsi.toFixed(0)} con fuerza compradora`)
    else if (rsi <= 30)
      parts.push(`RSI ${rsi.toFixed(0)} en sobreventa (puede haber suelo)`)
    else if (rsi <= 40) parts.push(`RSI ${rsi.toFixed(0)} con presión vendedora`)
    else parts.push(`RSI ${rsi.toFixed(0)} neutro`)
  }

  // MACD
  const macd = byKey.macd
  const macdSignal = byKey.macd_signal
  if (macd !== undefined && macdSignal !== undefined) {
    parts.push(
      macd > macdSignal
        ? "MACD apoya la subida"
        : "MACD apoya la bajada",
    )
  }

  // ADX (fuerza del movimiento, no dirección)
  const adx = byKey.adx
  if (adx !== undefined) {
    if (adx >= 25) parts.push(`ADX ${adx.toFixed(0)} (movimiento fuerte)`)
    else if (adx < 20) parts.push(`ADX ${adx.toFixed(0)} (poca fuerza)`)
  }

  // Bollinger (volatilidad)
  const bw = byKey.bb_bw
  if (bw !== undefined) {
    if (bw < 0.05) parts.push("Bandas comprimidas (volatilidad baja)")
    else if (bw > 0.15) parts.push("Bandas amplias (volatilidad alta)")
  }

  if (parts.length === 0) {
    return "Indicadores calculados — abre para ver valores."
  }
  return parts.slice(0, 3).join(" · ")
}

export function IndicatorSummaryCard({
  output,
  input,
}: IndicatorSummaryCardProps) {
  const data = "data" in output ? output.data : output
  const symbol = input.symbol?.toUpperCase() ?? "?"
  const tf = input.timeframe ?? "?"
  const { rows, byKey } = flattenLatest(data.latest)
  const interpretation = interpretIndicators(byKey)

  const grouped = groupByCategory(rows)

  return (
    <Collapsible
      className={cn(
        "group/ind w-full min-w-0 overflow-hidden rounded-md border border-border bg-card",
      )}
    >
      <CollapsibleTrigger
        className={cn(
          "flex w-full items-start gap-2 px-3 py-2 text-left",
          "transition-colors hover:bg-[var(--bg-2)]/50",
          "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-[-2px]",
        )}
      >
        <ActivityIcon
          className="mt-0.5 size-3.5 shrink-0 text-[var(--violet)]"
          aria-hidden
        />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
            indicadores · {symbol} {tf}
          </span>
          <p className="font-mono text-[12px] leading-snug text-foreground">
            {interpretation}
          </p>
        </div>
        <ChevronDownIcon
          className="mt-1 size-3 shrink-0 text-[var(--fg-3)] transition-transform group-data-[state=open]/ind:rotate-180"
          aria-hidden
        />
      </CollapsibleTrigger>
      <CollapsibleContent className="border-t border-[color:var(--line-soft)] px-3 py-2">
        {grouped.length === 0 ? (
          <p className="font-mono text-[11px] text-[var(--fg-3)]">
            sin datos en la ventana
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            {grouped.map(({ category, rows }) => (
              <div key={category} className="flex flex-col gap-1">
                <p className="font-mono text-[9px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
                  {CATEGORY_LABEL[category]}
                </p>
                <div className="grid min-w-0 grid-cols-2 gap-x-2 gap-y-1 sm:grid-cols-3">
                  {rows.map((row) => (
                    <IndicatorTile key={row.key} row={row} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </CollapsibleContent>
    </Collapsible>
  )
}

function IndicatorTile({ row }: { row: IndicatorRow }) {
  const description = INDICATOR_META[row.metaKey]?.description
  return (
    <div className="flex min-w-0 flex-col gap-0">
      <span className="inline-flex min-w-0 items-center gap-1 font-mono text-[9px] uppercase tracking-[0.1em] text-[var(--fg-3)]">
        <span className="truncate">{row.prettyName}</span>
        {description && (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                className="inline-flex size-3 shrink-0 items-center justify-center text-[var(--fg-4)] transition-colors hover:text-foreground"
                aria-label={`info ${row.prettyName}`}
              >
                <InfoIcon className="size-2.5" aria-hidden />
              </button>
            </TooltipTrigger>
            <TooltipContent>{description}</TooltipContent>
          </Tooltip>
        )}
      </span>
      <span className="truncate font-mono text-[11px] tabular-nums text-foreground">
        {fmt(row.value)}
      </span>
    </div>
  )
}

interface GroupedRows {
  category: Category
  rows: IndicatorRow[]
}

function groupByCategory(rows: IndicatorRow[]): GroupedRows[] {
  const buckets = new Map<Category, IndicatorRow[]>()
  for (const row of rows) {
    const cat = INDICATOR_META[row.metaKey]?.category ?? "other"
    const arr = buckets.get(cat) ?? []
    arr.push(row)
    buckets.set(cat, arr)
  }
  return CATEGORY_ORDER.filter((c) => buckets.has(c)).map((category) => ({
    category,
    rows: buckets.get(category)!,
  }))
}

function fmt(v: number | null): string {
  if (v === null) return "—"
  if (Math.abs(v) < 1000) return v.toFixed(2)
  return v.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

export function isIndicatorPanelOutput(
  value: unknown,
): value is ToolResultEnvelope<IndicatorPanelDTO> | IndicatorPanelDTO {
  if (!value || typeof value !== "object") return false
  const v = value as Record<string, unknown>
  const data = "data" in v ? (v.data as Record<string, unknown>) : v
  return (
    typeof data === "object" &&
    data !== null &&
    "asof" in data &&
    "latest" in data
  )
}
