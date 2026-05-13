"use client"

import { BoxesIcon, ChevronDownIcon } from "lucide-react"

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  isOpenInterest,
  type OiTrend,
  type OpenInterestDTO,
  type ToolResultEnvelope,
} from "@/lib/agent/outputs"
import { cn } from "@/lib/core/utils"

interface OpenInterestSummaryCardProps {
  output: ToolResultEnvelope<OpenInterestDTO> | OpenInterestDTO
  input: { symbol?: string }
}

const TREND_TONE: Record<
  OiTrend,
  { dot: string; text: string; label: string; long: string }
> = {
  rising: {
    dot: "bg-[var(--long)]",
    text: "text-[var(--long)]",
    label: "al alza",
    long: "OI creciendo: dinero nuevo entrando al instrumento. Si acompaña al precio, hay convicción real detrás del movimiento.",
  },
  falling: {
    dot: "bg-[var(--short)]",
    text: "text-[var(--short)]",
    label: "a la baja",
    long: "OI cayendo: posiciones cerrándose, unwinding. Convicción débil — el movimiento puede no sostenerse.",
  },
  stable: {
    dot: "bg-[var(--violet)]",
    text: "text-[var(--violet)]",
    label: "estable",
    long: "OI estable: ni nuevos entrantes ni cierre masivo — actividad de rotación pura.",
  },
}

function interpretOi(data: OpenInterestDTO): string {
  const tone = TREND_TONE[data.trend_7d]
  const sign = data.delta_24h_pct >= 0 ? "+" : ""
  return `${sign}${data.delta_24h_pct.toFixed(2)}% en 24h · tendencia 7d ${tone.label}`
}

function formatUsdt(usdt: number): string {
  if (usdt >= 1e9) return `${(usdt / 1e9).toFixed(2)}B`
  if (usdt >= 1e6) return `${(usdt / 1e6).toFixed(2)}M`
  if (usdt >= 1e3) return `${(usdt / 1e3).toFixed(1)}k`
  return usdt.toFixed(0)
}

/** Card resumen de get_open_interest — delta 24h + tendencia 7d. */
export function OpenInterestSummaryCard({
  output,
  input,
}: OpenInterestSummaryCardProps) {
  const data = "data" in output ? output.data : output
  const symbol = input.symbol?.toUpperCase() ?? data.symbol ?? "?"
  const tone = TREND_TONE[data.trend_7d]
  const interpretation = interpretOi(data)

  return (
    <Collapsible
      className={cn(
        "group/oi w-full min-w-0 overflow-hidden rounded-md border border-border bg-card",
      )}
    >
      <CollapsibleTrigger
        className={cn(
          "flex w-full items-start gap-2 px-3 py-2 text-left",
          "transition-colors hover:bg-[var(--bg-2)]/50",
          "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-[-2px]",
        )}
      >
        <BoxesIcon
          className="mt-0.5 size-3.5 shrink-0 text-[var(--violet)]"
          aria-hidden
        />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <div className="flex items-center gap-2">
            <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
              interés abierto · {symbol}
            </span>
            <span className={cn("dot ml-auto", tone.dot)} aria-hidden />
          </div>
          <p className="font-mono text-[12px] leading-snug text-foreground">
            {interpretation}
          </p>
        </div>
        <ChevronDownIcon
          className="mt-1 size-3 shrink-0 text-[var(--fg-3)] transition-transform group-data-[state=open]/oi:rotate-180"
          aria-hidden
        />
      </CollapsibleTrigger>
      <CollapsibleContent className="border-t border-[color:var(--line-soft)] px-3 py-2">
        <ul className="flex flex-col gap-1 font-mono text-[11px] tabular-nums">
          <li className="flex items-center justify-between">
            <span className="text-[var(--fg-3)]">OI actual (USDT)</span>
            <span className="text-foreground">
              ${formatUsdt(data.current_oi_usdt)}
            </span>
          </li>
          <li className="flex items-center justify-between">
            <span className="text-[var(--fg-3)]">delta 24h</span>
            <span className={cn(tone.text)}>
              {data.delta_24h_pct >= 0 ? "+" : ""}
              {data.delta_24h_pct.toFixed(2)}%
            </span>
          </li>
          <li className="flex items-center justify-between">
            <span className="text-[var(--fg-3)]">tendencia 7d</span>
            <span className={cn(tone.text)}>{tone.label}</span>
          </li>
        </ul>
        <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
          {tone.long}
        </p>
      </CollapsibleContent>
    </Collapsible>
  )
}

export function isOpenInterestOutput(
  value: unknown,
): value is ToolResultEnvelope<OpenInterestDTO> | OpenInterestDTO {
  if (!value || typeof value !== "object") return false
  const v = value as Record<string, unknown>
  const data = "data" in v ? v.data : v
  return isOpenInterest(data)
}
