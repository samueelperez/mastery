"use client"

import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import { Separator } from "@/components/ui/separator"
import { cn } from "@/lib/utils"
import type {
  Bias,
  Confidence,
  Direction,
  ToolCitation,
  TradeIdea,
} from "@/lib/chat-types"

interface TradeIdeaCardProps {
  idea: TradeIdea
}

/* Semantic palette (matches /research surface):
 *   primary (gold)   — long, bull, high-confidence, winning
 *   accent (purple)  — medium-confidence, "consider"
 *   destructive (red) — short, bear, losing
 *   muted            — no_trade, range, low-confidence
 */
const directionTone: Record<Direction, { label: string; cls: string }> = {
  long: { label: "LONG", cls: "bg-primary/15 text-primary border-primary/30" },
  short: {
    label: "SHORT",
    cls: "bg-destructive/15 text-destructive border-destructive/30",
  },
  no_trade: { label: "NO TRADE", cls: "bg-muted text-muted-foreground" },
}

const biasTone: Record<Bias, string> = {
  bull: "text-primary",
  bear: "text-destructive",
  range: "text-muted-foreground",
}

const confidenceTone: Record<Confidence, string> = {
  low: "bg-muted text-muted-foreground",
  medium: "bg-accent/15 text-accent border border-accent/40",
  high: "bg-primary/15 text-primary border border-primary/30",
}

function CitationBadges({ citations }: { citations: ToolCitation[] }) {
  if (citations.length === 0) return null
  return (
    <span className="ml-2 inline-flex items-center gap-1 align-middle">
      {citations.map((c, i) => (
        <HoverCard key={`${c.tool_name}-${i}`} openDelay={120} closeDelay={80}>
          <HoverCardTrigger asChild>
            <Badge
              variant="outline"
              className="cursor-help font-mono text-[9px] uppercase tracking-wider text-muted-foreground hover:text-foreground"
            >
              {c.tool_name.replace(/^get_/, "")}
            </Badge>
          </HoverCardTrigger>
          <HoverCardContent className="w-80 p-0">
            <div className="px-3 py-2">
              <div className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
                {c.tool_name}
              </div>
              <pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap break-all text-[11px] leading-relaxed">
                {JSON.stringify(c.snapshot, null, 2)}
              </pre>
            </div>
          </HoverCardContent>
        </HoverCard>
      ))}
    </span>
  )
}

function PriceCell({
  value,
  citations,
}: {
  value: number | null
  citations: ToolCitation[]
}) {
  if (value === null) return <span className="text-muted-foreground">—</span>
  return (
    <span className="font-mono">
      {value.toLocaleString(undefined, { minimumFractionDigits: 2 })}
      <CitationBadges citations={citations} />
    </span>
  )
}

export function TradeIdeaCard({ idea }: TradeIdeaCardProps) {
  const dir = directionTone[idea.direction]
  return (
    <Card className="border-border bg-card">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="font-mono text-sm tracking-tight">
            {idea.symbol} · {idea.timeframe}
          </CardTitle>
          <div className="flex items-center gap-2">
            <Badge variant="outline" className={cn("font-mono text-[11px]", dir.cls)}>
              {dir.label}
            </Badge>
            <Badge
              variant="outline"
              className={cn("font-mono text-[11px]", confidenceTone[idea.confidence])}
            >
              {idea.confidence.toUpperCase()}
            </Badge>
          </div>
        </div>
        <CardDescription className="pt-1 text-xs leading-relaxed text-muted-foreground">
          {idea.summary_es}
        </CardDescription>
      </CardHeader>

      <Separator />

      <CardContent className="space-y-3 pt-3 text-xs">
        {/* Regime */}
        <section>
          <div className="text-[11px] uppercase tracking-widest text-muted-foreground">
            Régimen
          </div>
          <div className="mt-1 flex items-center gap-2 font-mono">
            <span>{idea.regime.label.replace(/_/g, " ")}</span>
            <CitationBadges citations={idea.regime.citations} />
          </div>
        </section>

        {/* Confluences */}
        {idea.confluences.length > 0 && (
          <section>
            <div className="text-[11px] uppercase tracking-widest text-muted-foreground">
              Confluencias
            </div>
            <ul className="mt-1 space-y-1.5">
              {idea.confluences.map((c) => (
                <li key={c.timeframe} className="flex items-start gap-2">
                  <span className="w-10 font-mono text-muted-foreground">
                    {c.timeframe}
                  </span>
                  <span className={cn("w-12 font-mono uppercase", biasTone[c.bias])}>
                    {c.bias}
                  </span>
                  <span className="flex-1 text-muted-foreground">
                    {c.reasons.join(" · ")}
                  </span>
                  <CitationBadges citations={c.citations} />
                </li>
              ))}
            </ul>
          </section>
        )}

        {/* Entry / Invalidation / Targets */}
        <section className="space-y-1.5">
          <div className="text-[11px] uppercase tracking-widest text-muted-foreground">
            Niveles
          </div>
          <div className="grid grid-cols-[5rem_1fr] gap-x-3 gap-y-1.5 font-mono">
            <span className="text-muted-foreground">Entry</span>
            <PriceCell value={idea.entry} citations={idea.entry_citations} />
            {idea.entry_rationale && (
              <p className="col-span-2 -mt-0.5 text-[11px] text-muted-foreground">
                {idea.entry_rationale}
              </p>
            )}

            <span className="text-muted-foreground">Stop</span>
            <PriceCell
              value={idea.invalidation}
              citations={idea.invalidation_citations}
            />
            {idea.invalidation_rationale && (
              <p className="col-span-2 -mt-0.5 text-[11px] text-muted-foreground">
                {idea.invalidation_rationale}
              </p>
            )}

            {idea.targets.map((t) => (
              <div key={t.label} className="col-span-2 grid grid-cols-[5rem_1fr] gap-x-3">
                <span className="text-muted-foreground">{t.label}</span>
                <div className="space-y-0.5">
                  <PriceCell value={t.price} citations={t.citations} />
                  <p className="text-[11px] text-muted-foreground">{t.rationale}</p>
                </div>
              </div>
            ))}
          </div>
        </section>
      </CardContent>

      <Separator />

      <CardFooter className="pt-3 text-[11px] leading-relaxed text-muted-foreground">
        <div>
          <span className="font-mono uppercase tracking-widest">Risk</span>
          <span className="ml-2">{idea.risk_notes}</span>
        </div>
      </CardFooter>
    </Card>
  )
}
