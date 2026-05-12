"use client"

import { ChevronDownIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import type { PostMortemPayload } from "@/lib/ws"
import { cn } from "@/lib/utils"

interface PostMortemCardProps {
  postMortem: PostMortemPayload
}

const VERDICT_LABEL: Record<PostMortemPayload["verdict"], string> = {
  thesis_held: "TESIS CUMPLIDA",
  thesis_broken: "TESIS ROTA",
  execution_error: "ERROR EJECUCIÓN",
  noise: "RUIDO",
}

const VERDICT_TONE: Record<PostMortemPayload["verdict"], string> = {
  thesis_held:
    "bg-[var(--long-bg)] text-[var(--long)] border-[oklch(0.45_0.10_152_/_0.5)]",
  thesis_broken:
    "bg-[var(--short-bg)] text-[var(--short)] border-[oklch(0.45_0.18_22_/_0.5)]",
  execution_error:
    "bg-[var(--amber-soft)] text-[var(--amber)] border-[oklch(0.55_0.14_75_/_0.5)]",
  noise: "bg-muted text-foreground border-border",
}

const OUTCOME_LABEL: Record<PostMortemPayload["outcome"], string> = {
  win: "GANADO",
  loss: "PERDIDO",
  breakeven: "BREAKEVEN",
  partial_win: "PARCIAL",
}

const OUTCOME_TONE: Record<PostMortemPayload["outcome"], string> = {
  win: "bg-[var(--long-bg)] text-[var(--long)] border-[oklch(0.45_0.10_152_/_0.5)]",
  loss: "bg-[var(--short-bg)] text-[var(--short)] border-[oklch(0.45_0.18_22_/_0.5)]",
  breakeven: "bg-muted text-foreground border-border",
  partial_win:
    "bg-[var(--amber-soft)] text-[var(--amber)] border-[oklch(0.55_0.14_75_/_0.5)]",
}

const CALIBRATION_LABEL: Record<
  PostMortemPayload["confidence_calibration"],
  string
> = {
  over: "Sobre-confianza",
  under: "Sub-confianza",
  calibrated: "Calibración correcta",
}

const TRIGGER_LABEL: Record<PostMortemPayload["trigger_kind"], string> = {
  setup_closed_sl: "SL tocado",
  setup_closed_tp: "TP final tocado",
}

export function PostMortemCard({ postMortem: pm }: PostMortemCardProps) {
  const sideLabel = pm.side === "long" ? "LONG" : "SHORT"
  const sideTone =
    pm.side === "long"
      ? "bg-[var(--long-bg)] text-[var(--long)] border-[oklch(0.45_0.10_152_/_0.5)]"
      : "bg-[var(--short-bg)] text-[var(--short)] border-[oklch(0.45_0.18_22_/_0.5)]"

  const rText =
    typeof pm.r_multiple === "number"
      ? `${pm.r_multiple > 0 ? "+" : ""}${pm.r_multiple.toFixed(2)}R`
      : "—"

  return (
    <Card className="border-border bg-card">
      <CardHeader className="space-y-2 pb-3">
        <div className="flex items-center gap-2">
          <Badge variant="outline" className={cn("font-mono text-[10px]", sideTone)}>
            {sideLabel}
          </Badge>
          <span className="font-mono text-[12px] font-medium text-foreground">
            {pm.symbol}
          </span>
          <span className="font-mono text-[10px] uppercase tracking-wider text-[var(--fg-3)]">
            {pm.timeframe}
          </span>
          <span className="ml-auto font-mono text-[10px] uppercase tracking-wider text-[var(--fg-3)]">
            post-mortem
          </span>
        </div>
        <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
          <Badge variant="outline" className={cn("font-mono text-[10px]", VERDICT_TONE[pm.verdict])}>
            {VERDICT_LABEL[pm.verdict]}
          </Badge>
          <Badge variant="outline" className={cn("font-mono text-[10px]", OUTCOME_TONE[pm.outcome])}>
            {OUTCOME_LABEL[pm.outcome]} {rText}
          </Badge>
        </CardTitle>
        <CardDescription className="text-[12px] leading-snug text-foreground">
          {pm.lesson_es}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 pt-0">
        {(pm.success_factors.length > 0 || pm.failure_factors.length > 0) && (
          <div className="space-y-1.5">
            {pm.success_factors.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="font-mono text-[9px] uppercase tracking-wider text-[var(--fg-3)]">
                  ✓ funcionó
                </span>
                {pm.success_factors.map((f) => (
                  <Badge
                    key={`s-${f}`}
                    variant="outline"
                    className="font-mono text-[9px] bg-[var(--long-bg)] text-[var(--long)] border-[oklch(0.45_0.10_152_/_0.5)]"
                  >
                    {f}
                  </Badge>
                ))}
              </div>
            )}
            {pm.failure_factors.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="font-mono text-[9px] uppercase tracking-wider text-[var(--fg-3)]">
                  ✗ falló
                </span>
                {pm.failure_factors.map((f) => (
                  <Badge
                    key={`f-${f}`}
                    variant="outline"
                    className="font-mono text-[9px] bg-[var(--short-bg)] text-[var(--short)] border-[oklch(0.45_0.18_22_/_0.5)]"
                  >
                    {f}
                  </Badge>
                ))}
              </div>
            )}
          </div>
        )}

        <Collapsible>
          <CollapsibleTrigger className="group flex w-full items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-[var(--fg-2)] hover:text-foreground">
            <ChevronDownIcon className="h-3 w-3 transition-transform group-data-[state=open]:rotate-180" />
            Detalle
          </CollapsibleTrigger>
          <CollapsibleContent className="space-y-2 pt-2 text-[11px] text-[var(--fg-2)]">
            {pm.counterfactual_es && (
              <p className="whitespace-pre-wrap italic text-foreground/80">
                {pm.counterfactual_es}
              </p>
            )}
            {pm.mfe_mae && (
              <div className="space-y-0.5 font-mono text-[10px] text-[var(--fg-3)]">
                <div>
                  MFE: {pm.mfe_mae.mfe_r > 0 ? "+" : ""}
                  {pm.mfe_mae.mfe_r.toFixed(2)}R en {pm.mfe_mae.time_to_mfe_h}h
                </div>
                <div>
                  MAE: {pm.mfe_mae.mae_r > 0 ? "+" : ""}
                  {pm.mfe_mae.mae_r.toFixed(2)}R en {pm.mfe_mae.time_to_mae_h}h
                </div>
                {pm.mfe_mae.exit_efficiency_pct !== null && (
                  <div>
                    Eficiencia de salida: {pm.mfe_mae.exit_efficiency_pct}%
                  </div>
                )}
              </div>
            )}
            <div className="font-mono text-[10px] text-[var(--fg-3)]">
              Calibración: {CALIBRATION_LABEL[pm.confidence_calibration]}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {pm.citations.map((c, i) => (
                <Badge
                  key={`${c.tool_name}-${i}`}
                  variant="outline"
                  className="font-mono text-[9px]"
                >
                  {c.tool_name}
                </Badge>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-2 font-mono text-[10px] text-[var(--fg-3)]">
              <span>{TRIGGER_LABEL[pm.trigger_kind]}</span>
              <span>·</span>
              <span>
                {new Date(pm.created_at).toLocaleTimeString(undefined, {
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </span>
            </div>
          </CollapsibleContent>
        </Collapsible>
      </CardContent>
    </Card>
  )
}
