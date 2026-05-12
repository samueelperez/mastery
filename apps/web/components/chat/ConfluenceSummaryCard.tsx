"use client"

import { ChevronDownIcon, LayersIcon } from "lucide-react"

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { Progress } from "@/components/ui/progress"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type {
  ConfluenceMapDTO,
  ToolResultEnvelope,
} from "@/lib/agent/outputs"
import { cn } from "@/lib/core/utils"

interface ConfluenceSummaryCardProps {
  output: ToolResultEnvelope<ConfluenceMapDTO> | ConfluenceMapDTO
  input: { symbol?: string }
}

const BIAS_TONE: Record<
  string,
  { dot: string; text: string; bar: string; label: string }
> = {
  bull: {
    dot: "bg-[var(--long)]",
    text: "text-[var(--long)]",
    bar: "[&>div]:bg-[var(--long)]",
    label: "alcista",
  },
  bear: {
    dot: "bg-[var(--short)]",
    text: "text-[var(--short)]",
    bar: "[&>div]:bg-[var(--short)]",
    label: "bajista",
  },
  range: {
    dot: "bg-[var(--violet)]",
    text: "text-[var(--violet)]",
    bar: "[&>div]:bg-[var(--violet)]",
    label: "lateral",
  },
}

/** Genera la interpretación humana del payload — sin LLM, sólo lectura
 *  determinista de los campos. Lenguaje accesible para principiante/medio. */
function interpretConfluence(data: ConfluenceMapDTO): string {
  const total = data.by_tf.length
  const aligned = data.by_tf.filter(
    (tf) => tf.bias === data.aggregate_bias,
  ).length
  const pct = Math.round(data.aggregate_agreement_pct)
  const tfWord = total === 1 ? "marco temporal" : "marcos temporales"

  if (data.aggregate_bias === "range") {
    return `Sin tendencia clara — el precio está en rango lateral en ${aligned}/${total} ${tfWord}.`
  }
  const dir = data.aggregate_bias === "bull" ? "al alza" : "a la baja"
  if (pct >= 75) {
    return `Tendencia clara ${dir}: ${aligned}/${total} ${tfWord} coinciden (${pct}%).`
  }
  if (pct >= 50) {
    return `Tendencia ${dir} moderada: ${aligned} de ${total} ${tfWord} coinciden; el resto sin dirección clara.`
  }
  return `Señales contradictorias: sólo ${aligned}/${total} ${tfWord} apuntan ${dir}.`
}

/** Card resumen de get_multi_tf_confluence — modo slim row con
 *  interpretación + collapsible para detalles. */
export function ConfluenceSummaryCard({
  output,
  input,
}: ConfluenceSummaryCardProps) {
  const data = "data" in output ? output.data : output
  const symbol = input.symbol?.toUpperCase() ?? "?"
  const tone = BIAS_TONE[data.aggregate_bias] ?? BIAS_TONE.range!
  const interpretation = interpretConfluence(data)

  return (
    <Collapsible
      className={cn(
        "group/conf w-full min-w-0 overflow-hidden rounded-md border border-border bg-card",
      )}
    >
      <CollapsibleTrigger
        className={cn(
          "flex w-full items-start gap-2 px-3 py-2 text-left",
          "transition-colors hover:bg-[var(--bg-2)]/50",
          "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-[-2px]",
        )}
      >
        <LayersIcon
          className="mt-0.5 size-3.5 shrink-0 text-[var(--violet)]"
          aria-hidden
        />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <div className="flex items-center gap-2">
            <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
              confluencia · {symbol}
            </span>
            <span className={cn("dot ml-auto", tone.dot)} aria-hidden />
          </div>
          <p className="font-mono text-[12px] leading-snug text-foreground">
            {interpretation}
          </p>
        </div>
        <ChevronDownIcon
          className="mt-1 size-3 shrink-0 text-[var(--fg-3)] transition-transform group-data-[state=open]/conf:rotate-180"
          aria-hidden
        />
      </CollapsibleTrigger>
      <CollapsibleContent className="border-t border-[color:var(--line-soft)] px-3 py-2">
        <div className="mb-2 flex items-center gap-2">
          <Progress
            value={data.aggregate_agreement_pct}
            className={cn("h-1 flex-1 bg-[var(--bg-3)]", tone.bar)}
            aria-label={`acuerdo ${data.aggregate_agreement_pct.toFixed(0)}%`}
          />
          <span className="font-mono text-[10px] tabular-nums text-[var(--fg-2)]">
            {data.aggregate_agreement_pct.toFixed(0)}% acuerdo
          </span>
        </div>
        <ul className="flex flex-col gap-0">
          {data.by_tf.map((tf) => {
            const tfTone = BIAS_TONE[tf.bias] ?? BIAS_TONE.range!
            return (
              <Tooltip key={tf.timeframe}>
                <TooltipTrigger asChild>
                  <li className="flex min-w-0 cursor-default items-center gap-2 rounded px-1 py-1 hover:bg-[var(--bg-2)]/60">
                    <span className="w-7 shrink-0 font-mono text-[10px] uppercase tabular-nums text-foreground">
                      {tf.timeframe}
                    </span>
                    <span aria-hidden className={cn("dot", tfTone.dot)} />
                    <span className={cn("font-mono text-[10px]", tfTone.text)}>
                      {tfTone.label}
                    </span>
                    <span
                      className={cn(
                        "font-mono text-[10px] tabular-nums",
                        tf.score > 0
                          ? "text-[var(--long)]"
                          : tf.score < 0
                            ? "text-[var(--short)]"
                            : "text-[var(--fg-3)]",
                      )}
                    >
                      {tf.score >= 0 ? "+" : ""}
                      {tf.score}
                    </span>
                    <span className="ml-auto min-w-0 truncate font-mono text-[10px] text-[var(--fg-3)]">
                      {tf.reasons[0] ?? ""}
                    </span>
                  </li>
                </TooltipTrigger>
                <TooltipContent>
                  <div className="flex flex-col gap-0.5 text-[11px]">
                    <span className="font-mono text-[10px] uppercase opacity-80">
                      {tf.timeframe} · score {tf.score >= 0 ? "+" : ""}
                      {tf.score}
                    </span>
                    {tf.reasons.length === 0 ? (
                      <span className="opacity-60">sin razones</span>
                    ) : (
                      <ul className="flex flex-col gap-0.5">
                        {tf.reasons.map((r, i) => (
                          <li key={i}>· {r}</li>
                        ))}
                      </ul>
                    )}
                    {tf.last_close !== null && (
                      <span className="mt-1 text-[10px] opacity-60">
                        close {tf.last_close.toFixed(2)}
                        {tf.ema_21 !== null && ` · EMA21 ${tf.ema_21.toFixed(2)}`}
                      </span>
                    )}
                  </div>
                </TooltipContent>
              </Tooltip>
            )
          })}
        </ul>
      </CollapsibleContent>
    </Collapsible>
  )
}

export function isConfluenceMapOutput(
  value: unknown,
): value is ToolResultEnvelope<ConfluenceMapDTO> | ConfluenceMapDTO {
  if (!value || typeof value !== "object") return false
  const v = value as Record<string, unknown>
  const data = "data" in v ? (v.data as Record<string, unknown>) : v
  return (
    typeof data === "object" &&
    data !== null &&
    Array.isArray((data as Record<string, unknown>).by_tf) &&
    typeof (data as Record<string, unknown>).aggregate_bias === "string"
  )
}
