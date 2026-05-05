"use client"

import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  CircleDashedIcon,
  GaugeIcon,
} from "lucide-react"

import { Card, CardContent } from "@/components/ui/card"
import type { StrategyMetricsDTO } from "@/lib/api"
import { verdictOf } from "@/lib/backtest-verdict"

interface BacktestVerdictProps {
  metrics: StrategyMetricsDTO | null
}

/** Hero del detalle del backtest. Una Card grande con icono + título + 1
 *  frase plana en castellano explicando si este edge es real, marginal o
 *  basura. La lógica vive en `verdictOf` para compartirla con la lista. */
export function BacktestVerdict({ metrics }: BacktestVerdictProps) {
  const v = verdictOf(metrics)
  const Icon = (() => {
    switch (v.kind) {
      case "strong":
        return CheckCircle2Icon
      case "marginal":
        return GaugeIcon
      case "overfit":
        return AlertTriangleIcon
      case "weak":
        return GaugeIcon
      case "pending":
        return CircleDashedIcon
    }
  })()

  return (
    <Card
      className="border-0"
      style={{
        backgroundColor: v.bg,
        borderColor: v.border,
        borderWidth: 1,
        borderStyle: "solid",
      }}
    >
      <CardContent className="flex items-start gap-4 p-5">
        <div
          className="grid size-10 shrink-0 place-items-center rounded-md"
          style={{
            backgroundColor: v.bg,
            color: v.tone,
            border: `1px solid ${v.border}`,
          }}
        >
          <Icon className="size-5" aria-hidden />
        </div>
        <div className="flex flex-1 flex-col gap-1">
          <span
            className="font-mono text-[10px] uppercase tracking-[0.16em]"
            style={{ color: v.tone }}
          >
            veredicto
          </span>
          <p
            className="text-lg font-semibold tracking-tight"
            style={{ color: v.tone }}
          >
            {capitalize(v.label)}
          </p>
          <p className="text-[13px] leading-relaxed text-foreground/85">
            {v.copy}
          </p>
        </div>
      </CardContent>
    </Card>
  )
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1)
}
