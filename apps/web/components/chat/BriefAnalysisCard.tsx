"use client"

import { AlertTriangleIcon, OctagonAlertIcon } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import type { BriefAnalysis, Confidence } from "@/lib/chat-types"
import { cn } from "@/lib/utils"

import { KeyLevelsStrip } from "./KeyLevelsStrip"

interface BriefAnalysisCardProps {
  brief: BriefAnalysis
}

const confidenceLabel: Record<Confidence, string> = {
  high: "alta",
  medium: "media",
  low: "baja",
}

const confidenceCls: Record<Confidence, string> = {
  high: "border-[var(--long)]/40 text-[var(--long)]",
  medium: "border-border text-muted-foreground",
  // low destaca en amber: el modelo bajó la confianza por algo (data
  // stale, conflicto entre TFs) — el usuario debe verlo, no esconderlo.
  low: "border-[oklch(0.55_0.14_75_/_0.5)] text-[var(--amber)]",
}

/** Render exploratory analysis as plain prose — three short paragraphs, no
 * borders, no sections, no headers. The structure lives in the schema; the
 * presentation is just typography + spacing.
 *
 * Visual hierarchy:
 * - Header strip: symbol · timeframe + confidence pill (anchored, scannable)
 * - Verdict: foreground bold (the answer)
 * - Catalyst: muted (the reasoning)
 * - Risk: foreground/85 italic + alert icon (what kills the thesis)
 * - KeyLevels strip: color-coded chips
 */
export function BriefAnalysisCard({ brief }: BriefAnalysisCardProps) {
  return (
    <article className="flex flex-col gap-3 px-1 py-1.5">
      {brief.bias_alert && (
        <Alert className="border-[oklch(0.55_0.14_75_/_0.45)] bg-[var(--amber-soft)]">
          <AlertTriangleIcon className="size-4 text-[var(--amber)]" />
          <AlertTitle className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--amber)]">
            Aviso conductual · {brief.bias_alert.severity}
          </AlertTitle>
          <AlertDescription className="text-[12px] leading-relaxed text-foreground/85">
            {brief.bias_alert.message}
          </AlertDescription>
        </Alert>
      )}

      <header className="flex items-center justify-between gap-2">
        <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
          {brief.symbol} · {brief.timeframe}
        </span>
        <Badge
          variant="outline"
          className={cn(
            "font-mono text-[10px] uppercase tracking-[0.12em] tabular-nums",
            confidenceCls[brief.confidence],
          )}
        >
          confianza {confidenceLabel[brief.confidence]}
        </Badge>
      </header>

      <p className="text-[15px] font-medium leading-relaxed text-foreground">
        {brief.verdict_es}
      </p>
      <p className="text-[13.5px] leading-relaxed text-muted-foreground">
        {brief.catalyst_es}
      </p>

      <div className="flex items-start gap-2 rounded-sm border-l-2 border-[oklch(0.55_0.14_75_/_0.5)] bg-[var(--amber-soft)]/30 px-3 py-2">
        <OctagonAlertIcon className="mt-0.5 size-3.5 shrink-0 text-[var(--amber)]" />
        <p className="text-[13px] italic leading-relaxed text-foreground/85">
          {brief.risk_es}
        </p>
      </div>

      {brief.key_levels.length > 0 && (
        <KeyLevelsStrip
          levels={brief.key_levels}
          symbol={brief.symbol}
          className="mt-1"
        />
      )}
    </article>
  )
}
