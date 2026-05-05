"use client"

import { Card, CardContent } from "@/components/ui/card"
import type { OverviewKpi } from "@/lib/research-rollup"
import { cn } from "@/lib/utils"

interface ResumenHeroProps {
  kpi: OverviewKpi
}

/** Hero del resumen de /research. Sin números gigantes en mono — el
 *  protagonista es el verdict word en sans + 1 frase plain-language. */
export function ResumenHero({ kpi }: ResumenHeroProps) {
  const tone = toneFor(kpi.verdict)
  return (
    <Card className="border-border bg-card/40">
      <CardContent className="flex flex-col gap-4 p-6">
        <span className="eyebrow">resumen</span>
        <div className="flex flex-col gap-3">
          <h1
            className={cn(
              "text-[32px] font-semibold leading-tight tracking-tight",
            )}
            style={{ color: tone }}
          >
            {kpi.verdictLabel}
          </h1>
          <p className="max-w-2xl text-[14px] leading-relaxed text-foreground/85">
            {kpi.copy}
          </p>
        </div>
        {kpi.lastActivityMs !== null && (
          <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--fg-3)]">
            última actividad {formatRelative(kpi.lastActivityMs)}
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function toneFor(verdict: OverviewKpi["verdict"]): string {
  switch (verdict) {
    case "good":
      return "var(--long)"
    case "review":
      return "var(--short)"
    case "learning":
      return "var(--amber)"
    case "empty":
    default:
      return "var(--fg-2)"
  }
}

function formatRelative(ms: number): string {
  const diffMin = Math.round((Date.now() - ms) / 60_000)
  if (diffMin < 1) return "ahora"
  if (diffMin < 60) return `hace ${diffMin}m`
  const h = Math.round(diffMin / 60)
  if (h < 24) return `hace ${h}h`
  const d = Math.round(h / 24)
  return `hace ${d}d`
}
