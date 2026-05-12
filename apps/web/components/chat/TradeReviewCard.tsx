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
import type { TradeReviewPayload } from "@/lib/ws"
import { cn } from "@/lib/utils"

interface TradeReviewCardProps {
  review: TradeReviewPayload
}

const STATE_LABEL: Record<TradeReviewPayload["current_state"], string> = {
  on_track: "EN RUMBO",
  at_risk: "EN RIESGO",
  reversing: "REVIRTIENDO",
}

const STATE_TONE: Record<TradeReviewPayload["current_state"], string> = {
  on_track: "bg-[var(--long-bg)] text-[var(--long)] border-[oklch(0.45_0.10_152_/_0.5)]",
  at_risk: "bg-[var(--amber-soft)] text-[var(--amber)] border-[oklch(0.55_0.14_75_/_0.5)]",
  reversing:
    "bg-[var(--short-bg)] text-[var(--short)] border-[oklch(0.45_0.18_22_/_0.5)]",
}

const REC_LABEL: Record<TradeReviewPayload["recommendation"], string> = {
  hold: "MANTENER",
  tighten_sl: "AJUSTAR SL",
  partial_close: "CERRAR PARCIAL",
  exit_now: "SALIR YA",
}

const REC_TONE: Record<TradeReviewPayload["recommendation"], string> = {
  hold: "bg-muted text-foreground border-border",
  tighten_sl: "bg-primary/15 text-primary border-primary/30",
  partial_close: "bg-[var(--amber-soft)] text-[var(--amber)] border-[oklch(0.55_0.14_75_/_0.5)]",
  exit_now: "bg-destructive/15 text-destructive border-destructive/30",
}

const TRIGGER_LABEL: Record<TradeReviewPayload["trigger_kind"], string> = {
  entry_hit: "Tras toque de entry",
  tp_partial: "TP parcial alcanzado",
  time_elapsed: "Tiempo transcurrido",
  price_move: "Movimiento de precio",
  approaching_sl: "Cerca del SL",
  regime_change: "Cambio de régimen",
}

export function TradeReviewCard({ review }: TradeReviewCardProps) {
  const sideLabel = review.side === "long" ? "LONG" : "SHORT"
  const sideTone =
    review.side === "long"
      ? "bg-[var(--long-bg)] text-[var(--long)] border-[oklch(0.45_0.10_152_/_0.5)]"
      : "bg-[var(--short-bg)] text-[var(--short)] border-[oklch(0.45_0.18_22_/_0.5)]"

  return (
    <Card className="border-border bg-card">
      <CardHeader className="space-y-2 pb-3">
        <div className="flex items-center gap-2">
          <Badge variant="outline" className={cn("font-mono text-[10px]", sideTone)}>
            {sideLabel}
          </Badge>
          <span className="font-mono text-[12px] font-medium text-foreground">
            {review.symbol}
          </span>
          <span className="font-mono text-[10px] uppercase tracking-wider text-[var(--fg-3)]">
            {review.timeframe}
          </span>
          <span className="ml-auto font-mono text-[10px] uppercase tracking-wider text-[var(--fg-3)]">
            revisión
          </span>
        </div>
        <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
          <Badge variant="outline" className={cn("font-mono text-[10px]", STATE_TONE[review.current_state])}>
            {STATE_LABEL[review.current_state]}
          </Badge>
          <Badge variant="outline" className={cn("font-mono text-[10px]", REC_TONE[review.recommendation])}>
            {REC_LABEL[review.recommendation]}
          </Badge>
        </CardTitle>
        <CardDescription className="text-[12px] leading-snug text-foreground">
          {review.summary}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2 pt-0">
        <Collapsible>
          <CollapsibleTrigger className="group flex w-full items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-[var(--fg-2)] hover:text-foreground">
            <ChevronDownIcon className="h-3 w-3 transition-transform group-data-[state=open]:rotate-180" />
            Detalle
          </CollapsibleTrigger>
          <CollapsibleContent className="space-y-2 pt-2 text-[11px] text-[var(--fg-2)]">
            <p className="whitespace-pre-wrap text-foreground/90">
              {review.rationale}
            </p>
            <div className="flex flex-wrap gap-1.5">
              {review.citations.map((c, i) => (
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
              <span>{TRIGGER_LABEL[review.trigger_kind]}</span>
              <span>·</span>
              <span>precio {review.price_at_review}</span>
              <span>·</span>
              <span>
                {new Date(review.created_at).toLocaleTimeString(undefined, {
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
