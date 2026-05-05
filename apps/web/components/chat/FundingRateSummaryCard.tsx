"use client"

import { ChevronDownIcon, PercentIcon } from "lucide-react"

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  isFundingRate,
  type FundingBias,
  type FundingRateDTO,
  type ToolResultEnvelope,
} from "@/lib/agent-outputs"
import { cn } from "@/lib/utils"

interface FundingRateSummaryCardProps {
  output: ToolResultEnvelope<FundingRateDTO> | FundingRateDTO
  input: { symbol?: string }
}

const BIAS_TONE: Record<
  FundingBias,
  { dot: string; text: string; short: string; long: string }
> = {
  // long_pays = longs apilados / pagan a shorts → riesgo de squeeze bajista
  // mostramos rojo (negativo para nuevos longs).
  long_pays: {
    dot: "bg-[var(--short)]",
    text: "text-[var(--short)]",
    short: "longs pagan",
    long: "Los longs pagan a los shorts — mercado apilado al alza, riesgo de squeeze bajista.",
  },
  // short_pays = shorts apilados → favorable a entradas long
  short_pays: {
    dot: "bg-[var(--long)]",
    text: "text-[var(--long)]",
    short: "shorts pagan",
    long: "Los shorts pagan a los longs — mercado apilado a la baja, posible squeeze alcista.",
  },
  neutral: {
    dot: "bg-[var(--violet)]",
    text: "text-[var(--violet)]",
    short: "neutro",
    long: "Tasas balanceadas — sin sesgo claro de derivados.",
  },
}

function interpretFunding(data: FundingRateDTO): string {
  const tone = BIAS_TONE[data.bias]
  const sign = data.current_rate_pct >= 0 ? "+" : ""
  return `${sign}${data.current_rate_pct.toFixed(4)}%/8h · ${tone.short} · 7d ${data.cumulative_7d_pct >= 0 ? "+" : ""}${data.cumulative_7d_pct.toFixed(3)}%`
}

/** Card resumen de get_funding_rate — tasa actual + bias + acumulado 7d. */
export function FundingRateSummaryCard({
  output,
  input,
}: FundingRateSummaryCardProps) {
  const data = "data" in output ? output.data : output
  const symbol = input.symbol?.toUpperCase() ?? data.symbol ?? "?"
  const tone = BIAS_TONE[data.bias]
  const interpretation = interpretFunding(data)

  let nextFundingLabel = ""
  try {
    const dt = new Date(data.next_funding_ts)
    if (!Number.isNaN(dt.getTime())) {
      nextFundingLabel = dt.toLocaleString(undefined, {
        hour: "2-digit",
        minute: "2-digit",
        day: "2-digit",
        month: "2-digit",
      })
    }
  } catch {
    nextFundingLabel = ""
  }

  return (
    <Collapsible
      className={cn(
        "group/fr w-full min-w-0 overflow-hidden rounded-md border border-border bg-card",
      )}
    >
      <CollapsibleTrigger
        className={cn(
          "flex w-full items-start gap-2 px-3 py-2 text-left",
          "transition-colors hover:bg-[var(--bg-2)]/50",
          "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-[-2px]",
        )}
      >
        <PercentIcon
          className="mt-0.5 size-3.5 shrink-0 text-[var(--violet)]"
          aria-hidden
        />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <div className="flex items-center gap-2">
            <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
              funding · {symbol}
            </span>
            <span className={cn("dot ml-auto", tone.dot)} aria-hidden />
          </div>
          <p className="font-mono text-[12px] leading-snug text-foreground">
            {interpretation}
          </p>
        </div>
        <ChevronDownIcon
          className="mt-1 size-3 shrink-0 text-[var(--fg-3)] transition-transform group-data-[state=open]/fr:rotate-180"
          aria-hidden
        />
      </CollapsibleTrigger>
      <CollapsibleContent className="border-t border-[color:var(--line-soft)] px-3 py-2">
        <ul className="flex flex-col gap-1 font-mono text-[11px] tabular-nums">
          <li className="flex items-center justify-between">
            <span className="text-[var(--fg-3)]">tasa actual</span>
            <span className={cn(tone.text)}>
              {data.current_rate_pct >= 0 ? "+" : ""}
              {data.current_rate_pct.toFixed(4)}%/8h
            </span>
          </li>
          <li className="flex items-center justify-between">
            <span className="text-[var(--fg-3)]">media 7d</span>
            <span className="text-foreground">
              {data.avg_7d_pct >= 0 ? "+" : ""}
              {data.avg_7d_pct.toFixed(4)}%/8h
            </span>
          </li>
          <li className="flex items-center justify-between">
            <span className="text-[var(--fg-3)]">acumulado 7d</span>
            <span className="text-foreground">
              {data.cumulative_7d_pct >= 0 ? "+" : ""}
              {data.cumulative_7d_pct.toFixed(3)}%
            </span>
          </li>
          {nextFundingLabel && (
            <li className="flex items-center justify-between">
              <span className="text-[var(--fg-3)]">próximo pago</span>
              <span className="text-foreground">{nextFundingLabel}</span>
            </li>
          )}
        </ul>
        <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
          {tone.long}
        </p>
      </CollapsibleContent>
    </Collapsible>
  )
}

export function isFundingRateOutput(
  value: unknown,
): value is ToolResultEnvelope<FundingRateDTO> | FundingRateDTO {
  if (!value || typeof value !== "object") return false
  const v = value as Record<string, unknown>
  const data = "data" in v ? v.data : v
  return isFundingRate(data)
}
