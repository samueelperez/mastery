"use client"

import { BarChart3Icon, ChevronDownIcon } from "lucide-react"

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  isVolumeProfile,
  type ToolResultEnvelope,
  type VolumeProfileDTO,
} from "@/lib/agent/outputs"
import { cn } from "@/lib/core/utils"

interface VolumeProfileSummaryCardProps {
  output: ToolResultEnvelope<VolumeProfileDTO> | VolumeProfileDTO
  input: { symbol?: string; timeframe?: string }
}

function formatPrice(price: number): string {
  if (price >= 1000) return price.toLocaleString(undefined, { maximumFractionDigits: 1 })
  if (price >= 1) return price.toLocaleString(undefined, { maximumFractionDigits: 3 })
  return price.toLocaleString(undefined, { maximumFractionDigits: 6 })
}

/** Resumen determinista del shape: dónde está el POC y cuántos HVN/LVN
 *  hay arriba y abajo. Sin LLM. */
function interpretVolumeProfile(data: VolumeProfileDTO): string {
  const n_hvn = data.high_volume_nodes.length
  const n_lvn = data.low_volume_nodes.length
  if (n_hvn === 0 && n_lvn === 0) {
    return `POC ${formatPrice(data.poc_price)} · sin nodos relevantes en el rango.`
  }
  const partes: string[] = [`POC ${formatPrice(data.poc_price)}`]
  if (n_hvn > 0) {
    partes.push(`${n_hvn} ${n_hvn === 1 ? "zona aceptada" : "zonas aceptadas"}`)
  }
  if (n_lvn > 0) {
    partes.push(`${n_lvn} ${n_lvn === 1 ? "vacío" : "vacíos"}`)
  }
  return partes.join(" · ")
}

/** Card resumen de get_volume_profile — POC + HVN/LVN counts y top-3 de
 *  cada categoría en el detalle. */
export function VolumeProfileSummaryCard({
  output,
  input,
}: VolumeProfileSummaryCardProps) {
  const data = "data" in output ? output.data : output
  const symbol = input.symbol?.toUpperCase() ?? data.symbol ?? "?"
  const tf = (input.timeframe ?? data.timeframe ?? "").toUpperCase()
  const interpretation = interpretVolumeProfile(data)

  // Status dot: el POC suele estar dentro de [range_low, range_high] —
  // usamos su posición relativa (sólo para señalizar visualmente la
  // distribución, sin tono direccional fuerte).
  const rangeMid = (data.range_low + data.range_high) / 2
  const dotCls =
    data.poc_price > rangeMid * 1.003
      ? "bg-[var(--long)]"
      : data.poc_price < rangeMid * 0.997
        ? "bg-[var(--short)]"
        : "bg-[var(--violet)]"

  const topHvn = [...data.high_volume_nodes]
    .sort((a, b) => b.pct_of_poc - a.pct_of_poc)
    .slice(0, 3)
  const topLvn = [...data.low_volume_nodes]
    .sort((a, b) => a.pct_of_poc - b.pct_of_poc)
    .slice(0, 3)

  return (
    <Collapsible
      className={cn(
        "group/vp w-full min-w-0 overflow-hidden rounded-md border border-border bg-card",
      )}
    >
      <CollapsibleTrigger
        className={cn(
          "flex w-full items-start gap-2 px-3 py-2 text-left",
          "transition-colors hover:bg-[var(--bg-2)]/50",
          "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-[-2px]",
        )}
      >
        <BarChart3Icon
          className="mt-0.5 size-3.5 shrink-0 text-[var(--violet)]"
          aria-hidden
        />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <div className="flex items-center gap-2">
            <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
              volumen · {symbol}
              {tf && ` ${tf}`}
            </span>
            <span className={cn("dot ml-auto", dotCls)} aria-hidden />
          </div>
          <p className="font-mono text-[12px] leading-snug text-foreground">
            {interpretation}
          </p>
        </div>
        <ChevronDownIcon
          className="mt-1 size-3 shrink-0 text-[var(--fg-3)] transition-transform group-data-[state=open]/vp:rotate-180"
          aria-hidden
        />
      </CollapsibleTrigger>
      <CollapsibleContent className="border-t border-[color:var(--line-soft)] px-3 py-2">
        <div className="mb-2 flex items-center justify-between gap-2 font-mono text-[10px] tabular-nums text-[var(--fg-2)]">
          <span>
            rango {formatPrice(data.range_low)}–{formatPrice(data.range_high)}
          </span>
          <span className="text-[var(--fg-3)]">{data.lookback_bars} barras</span>
        </div>
        {topHvn.length > 0 && (
          <div className="mb-2">
            <div className="mb-1 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
              zonas de alta aceptación
            </div>
            <ul className="flex flex-col gap-0">
              {topHvn.map((n, i) => (
                <li
                  key={`hvn-${i}`}
                  className="flex items-center gap-2 rounded px-1 py-0.5 hover:bg-[var(--bg-2)]/60"
                >
                  <span aria-hidden className="dot bg-[var(--long)]" />
                  <span className="font-mono text-[11px] tabular-nums text-foreground">
                    {formatPrice(n.price)}
                  </span>
                  <span className="ml-auto font-mono text-[10px] tabular-nums text-[var(--fg-3)]">
                    {n.pct_of_poc.toFixed(0)}% del POC
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {topLvn.length > 0 && (
          <div>
            <div className="mb-1 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
              vacíos de volumen
            </div>
            <ul className="flex flex-col gap-0">
              {topLvn.map((n, i) => (
                <li
                  key={`lvn-${i}`}
                  className="flex items-center gap-2 rounded px-1 py-0.5 hover:bg-[var(--bg-2)]/60"
                >
                  <span aria-hidden className="dot bg-[var(--short)]" />
                  <span className="font-mono text-[11px] tabular-nums text-foreground">
                    {formatPrice(n.price)}
                  </span>
                  <span className="ml-auto font-mono text-[10px] tabular-nums text-[var(--fg-3)]">
                    {n.pct_of_poc.toFixed(0)}% del POC
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </CollapsibleContent>
    </Collapsible>
  )
}

export function isVolumeProfileOutput(
  value: unknown,
): value is ToolResultEnvelope<VolumeProfileDTO> | VolumeProfileDTO {
  if (!value || typeof value !== "object") return false
  const v = value as Record<string, unknown>
  const data = "data" in v ? v.data : v
  return isVolumeProfile(data)
}
