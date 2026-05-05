"use client"

import { ChevronDownIcon, GitCompareIcon } from "lucide-react"

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  isBtcCorrelation,
  type BtcCorrelationDTO,
  type ToolResultEnvelope,
} from "@/lib/agent-outputs"
import { cn } from "@/lib/utils"

interface BtcCorrelationSummaryCardProps {
  output: ToolResultEnvelope<BtcCorrelationDTO> | BtcCorrelationDTO
  input: { symbol?: string; timeframe?: string }
}

/** Devuelve descripción + tono del peso relativo del bias de BTC. */
function bandFor(weight: number): {
  shortLabel: string
  longLabel: string
  dot: string
  show_dot: boolean
} {
  if (weight >= 0.85) {
    return {
      shortLabel: "alt sigue a BTC casi 1:1",
      longLabel:
        "Correlación alta: el bias de BTC explica casi todo el movimiento. Tomar bias direccional aislado en este alt es ignorar al driver real.",
      dot: "bg-[var(--violet)]",
      show_dot: true,
    }
  }
  if (weight >= 0.5) {
    return {
      shortLabel: "bias parcialmente heredado",
      longLabel:
        "Correlación moderada: BTC influye pero el alt tiene movimiento propio. Pondera el bias de BTC al ~50% de la lectura.",
      dot: "bg-[var(--fg-3)]",
      show_dot: true,
    }
  }
  return {
    shortLabel: "movimiento propio",
    longLabel:
      "Correlación baja: el alt se mueve por dinámica propia. El bias de BTC es contexto, no determinante.",
    dot: "",
    show_dot: false,
  }
}

function interpretCorrelation(data: BtcCorrelationDTO): string {
  const band = bandFor(data.bias_weight_factor)
  return `Pearson ${data.pearson.toFixed(2)} · ${band.shortLabel}`
}

/** Card resumen de get_btc_correlation — pearson + cuánto del bias se hereda
 *  de BTC. Solo aparece para alts (la tool no se llama para BTCUSDT). */
export function BtcCorrelationSummaryCard({
  output,
  input,
}: BtcCorrelationSummaryCardProps) {
  const data = "data" in output ? output.data : output
  const symbol = input.symbol?.toUpperCase() ?? data.symbol ?? "?"
  const tf = (input.timeframe ?? data.timeframe ?? "").toUpperCase()
  const band = bandFor(data.bias_weight_factor)
  const interpretation = interpretCorrelation(data)

  return (
    <Collapsible
      className={cn(
        "group/cor w-full min-w-0 overflow-hidden rounded-md border border-border bg-card",
      )}
    >
      <CollapsibleTrigger
        className={cn(
          "flex w-full items-start gap-2 px-3 py-2 text-left",
          "transition-colors hover:bg-[var(--bg-2)]/50",
          "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-[-2px]",
        )}
      >
        <GitCompareIcon
          className="mt-0.5 size-3.5 shrink-0 text-[var(--violet)]"
          aria-hidden
        />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <div className="flex items-center gap-2">
            <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
              correlación btc · {symbol}
              {tf && ` ${tf}`}
            </span>
            {band.show_dot && (
              <span className={cn("dot ml-auto", band.dot)} aria-hidden />
            )}
          </div>
          <p className="font-mono text-[12px] leading-snug text-foreground">
            {interpretation}
          </p>
        </div>
        <ChevronDownIcon
          className="mt-1 size-3 shrink-0 text-[var(--fg-3)] transition-transform group-data-[state=open]/cor:rotate-180"
          aria-hidden
        />
      </CollapsibleTrigger>
      <CollapsibleContent className="border-t border-[color:var(--line-soft)] px-3 py-2">
        <ul className="flex flex-col gap-1 font-mono text-[11px] tabular-nums">
          <li className="flex items-center justify-between">
            <span className="text-[var(--fg-3)]">Pearson (vs BTCUSDT)</span>
            <span className="text-foreground">{data.pearson.toFixed(4)}</span>
          </li>
          <li className="flex items-center justify-between">
            <span className="text-[var(--fg-3)]">peso del bias BTC</span>
            <span className="text-foreground">
              {(data.bias_weight_factor * 100).toFixed(0)}%
            </span>
          </li>
          <li className="flex items-center justify-between">
            <span className="text-[var(--fg-3)]">ventana</span>
            <span className="text-foreground">
              {data.lookback_bars} barras
            </span>
          </li>
        </ul>
        <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
          {band.longLabel}
        </p>
      </CollapsibleContent>
    </Collapsible>
  )
}

export function isBtcCorrelationOutput(
  value: unknown,
): value is ToolResultEnvelope<BtcCorrelationDTO> | BtcCorrelationDTO {
  if (!value || typeof value !== "object") return false
  const v = value as Record<string, unknown>
  const data = "data" in v ? v.data : v
  return isBtcCorrelation(data)
}
