"use client"

import { AlertTriangleIcon, ChevronDownIcon } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
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
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import { Separator } from "@/components/ui/separator"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
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

interface RiskReward {
  ratio: number
  risk: number
  reward: number
  firstTpLabel: string
  /** "good" ≥ 2, "ok" ≥ 1.5, "weak" ≥ 1, "bad" < 1 (no debería pasar el
   *  validator del backend, pero defendemos visualmente por si acaso). */
  tone: "good" | "ok" | "weak" | "bad"
}

function computeRiskReward(idea: TradeIdea): RiskReward | null {
  if (idea.direction === "no_trade") return null
  if (idea.entry === null || idea.invalidation === null) return null
  if (idea.targets.length === 0) return null
  const risk = Math.abs(idea.entry - idea.invalidation)
  if (risk === 0) return null
  const firstTp = idea.targets[0]!
  const reward =
    idea.direction === "long"
      ? firstTp.price - idea.entry
      : idea.entry - firstTp.price
  const ratio = reward / risk
  const tone: RiskReward["tone"] =
    ratio >= 2 ? "good" : ratio >= 1.5 ? "ok" : ratio >= 1 ? "weak" : "bad"
  return {
    ratio,
    risk,
    reward,
    firstTpLabel: firstTp.label,
    tone,
  }
}

const RR_TONE_CLS: Record<RiskReward["tone"], string> = {
  good: "bg-[var(--long-bg)] text-[var(--long)] border-[oklch(0.45_0.10_152_/_0.5)]",
  ok: "bg-[var(--violet-soft)] text-[var(--violet)] border-[oklch(0.55_0.16_290_/_0.5)]",
  weak: "bg-[var(--amber-soft)] text-[var(--amber)] border-[oklch(0.55_0.14_75_/_0.5)]",
  bad: "bg-[var(--short-bg)] text-[var(--short)] border-[oklch(0.45_0.18_22_/_0.5)]",
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

/** Tinte direccional aplicado al `<Card>` entera. Border-left de 4px hace de
 *  etiqueta visual; el gradiente decae a `bg-card` para no abrumar. `no_trade`
 *  se queda neutro a propósito — no es un evento direccional. */
const directionCardCls: Record<Direction, string> = {
  long:
    "border-l-4 border-l-[var(--long)] " +
    "border-[oklch(0.45_0.10_152_/_0.4)] " +
    "bg-gradient-to-br from-[var(--long-bg)]/40 to-card",
  short:
    "border-l-4 border-l-[var(--short)] " +
    "border-[oklch(0.45_0.18_22_/_0.4)] " +
    "bg-gradient-to-br from-[var(--short-bg)]/40 to-card",
  no_trade: "border-border bg-card",
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

function CitationBadges({ citations }: { citations: ToolCitation[] | undefined }) {
  if (!citations || citations.length === 0) return null
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
  const rr = computeRiskReward(idea)
  return (
    <Card className={cn(directionCardCls[idea.direction])}>
      {idea.bias_alert && (
        <div className="px-6 pt-4">
          <Alert className="border-[oklch(0.55_0.14_75_/_0.45)] bg-[var(--amber-soft)]">
            <AlertTriangleIcon className="size-4 text-[var(--amber)]" />
            <AlertTitle className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--amber)]">
              Aviso conductual · {idea.bias_alert.severity}
            </AlertTitle>
            <AlertDescription className="text-[12px] leading-relaxed text-foreground/85">
              {idea.bias_alert.message}
            </AlertDescription>
          </Alert>
        </div>
      )}
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="font-mono text-sm tracking-tight">
            {idea.symbol} · {idea.timeframe}
          </CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline" className={cn("font-mono text-[11px]", dir.cls)}>
              {dir.label}
            </Badge>
            {rr && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Badge
                    variant="outline"
                    className={cn(
                      "cursor-help font-mono text-[11px] tabular-nums",
                      RR_TONE_CLS[rr.tone],
                    )}
                  >
                    R:R {rr.ratio.toFixed(2)}
                    {rr.tone === "bad" && " ⚠"}
                    {rr.tone === "weak" && " ⚠"}
                  </Badge>
                </TooltipTrigger>
                <TooltipContent>
                  <div className="flex flex-col gap-0.5 text-[11px]">
                    <span>
                      Reward {rr.reward.toFixed(2)} / Risk {rr.risk.toFixed(2)} al{" "}
                      {rr.firstTpLabel}
                    </span>
                    <span className="opacity-70">
                      {rr.tone === "good"
                        ? "Excelente — esperanza positiva clara."
                        : rr.tone === "ok"
                          ? "Aceptable — mínimo para esperanza positiva."
                          : rr.tone === "weak"
                            ? "Pobre — apenas cubre fees + slippage."
                            : "Negativo — esperanza matemática negativa."}
                    </span>
                  </div>
                </TooltipContent>
              </Tooltip>
            )}
            {typeof idea.position_size_pct === "number" &&
              typeof idea.leverage_x === "number" && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Badge
                      variant="outline"
                      className="cursor-help font-mono text-[11px] tabular-nums"
                    >
                      {idea.position_size_pct.toFixed(1)}% ·{" "}
                      {idea.leverage_x.toFixed(0)}×
                    </Badge>
                  </TooltipTrigger>
                  <TooltipContent>
                    <span className="text-[11px]">
                      Tamaño de posición {idea.position_size_pct.toFixed(1)}% del
                      equity con {idea.leverage_x.toFixed(0)}× de leverage.
                    </span>
                  </TooltipContent>
                </Tooltip>
              )}
            <Badge
              variant="outline"
              className={cn(
                "font-mono text-[11px]",
                idea.confidence ? confidenceTone[idea.confidence] : undefined,
              )}
            >
              {(idea.confidence ?? "—").toUpperCase()}
            </Badge>
          </div>
        </div>
        <CardDescription className="pt-1 text-xs leading-relaxed text-muted-foreground">
          {idea.summary_es}
        </CardDescription>
      </CardHeader>

      {idea.scenarios && idea.scenarios.length >= 2 && (
        <>
          <Separator />
          <CardContent className="py-3 text-xs">
            <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
              Escenarios
            </div>
            <ul className="flex flex-col gap-1.5">
              {idea.scenarios.map((s) => (
                <li key={s.label} className="flex items-baseline gap-2">
                  <Badge
                    variant="outline"
                    className="shrink-0 font-mono text-[10px] tabular-nums"
                  >
                    {s.label} · {s.probability_pct}%
                  </Badge>
                  <span className="min-w-0 flex-1 leading-relaxed text-muted-foreground">
                    {s.description}
                    {typeof s.entry === "number" &&
                      typeof s.invalidation === "number" && (
                        <span className="ml-2 font-mono text-[11px] tabular-nums text-foreground/80">
                          {" — "}entry {s.entry.toLocaleString()} · SL{" "}
                          {s.invalidation.toLocaleString()}
                          {typeof s.target === "number" &&
                            ` · TP ${s.target.toLocaleString()}`}
                        </span>
                      )}
                  </span>
                </li>
              ))}
            </ul>
          </CardContent>
        </>
      )}

      {idea.direction !== "no_trade" && <Separator />}

      {idea.direction !== "no_trade" && (
      <CardContent className="pt-3 text-xs">
        {/* Niveles compactos: precio + 1 línea de rationale truncado.
         *  Click en cualquier label/precio expande con HoverCard si necesita
         *  ver el rationale completo + citations.
         *  Sólo se renderizan cuando hay setup activo (long/short). En
         *  no_trade los niveles serían dashes huecos — más ruido que señal. */}
        <ul className="grid grid-cols-[3.5rem_1fr] gap-x-3 gap-y-1.5 font-mono">
          <LevelRow
            label="Entry"
            price={idea.entry}
            rationale={idea.entry_rationale}
            citations={idea.entry_citations}
          />
          <LevelRow
            label="Stop"
            price={idea.invalidation}
            rationale={idea.invalidation_rationale}
            citations={idea.invalidation_citations}
          />
          {idea.targets.map((t) => (
            <LevelRow
              key={t.label}
              label={t.label}
              price={t.price}
              rationale={t.rationale}
              citations={t.citations}
            />
          ))}
        </ul>
      </CardContent>
      )}

      {/* Análisis completo: régimen + confluencias narrativas + risk_notes.
       *  CERRADO por defecto — la card debe ser ligera; el detalle se expande
       *  bajo demanda. */}
      <Collapsible className="group/full">
        <CollapsibleTrigger
          className={cn(
            "flex w-full items-center gap-2 border-t border-[color:var(--line-soft)]",
            "px-6 py-2 text-left transition-colors hover:bg-[var(--bg-2)]/40",
            "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-[-2px]",
          )}
        >
          <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
            análisis completo
          </span>
          <span className="font-mono text-[10px] tabular-nums text-[var(--fg-4)]">
            · {idea.confluences.length}{" "}
            {idea.confluences.length === 1 ? "confluencia" : "confluencias"}
          </span>
          <ChevronDownIcon
            className="ml-auto size-3 text-[var(--fg-3)] transition-transform group-data-[state=open]/full:rotate-180"
            aria-hidden
          />
        </CollapsibleTrigger>
        <CollapsibleContent className="space-y-3 border-t border-[color:var(--line-soft)] px-6 py-3 text-xs">
          {/* Régimen */}
          <section>
            <div className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
              Régimen
            </div>
            <div className="mt-1 flex items-center gap-2 font-mono">
              <span>{idea.regime.label.replace(/_/g, " ")}</span>
              <CitationBadges citations={idea.regime.citations} />
            </div>
          </section>

          {/* Confluencias detalladas */}
          {idea.confluences.length > 0 && (
            <section>
              <div className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                Confluencias
              </div>
              <ul className="mt-1 space-y-1.5">
                {idea.confluences.map((c) => (
                  <li
                    key={c.timeframe}
                    className="flex min-w-0 items-start gap-2"
                  >
                    <span className="w-8 shrink-0 font-mono text-muted-foreground">
                      {c.timeframe}
                    </span>
                    <span
                      className={cn(
                        "w-12 shrink-0 font-mono uppercase",
                        biasTone[c.bias],
                      )}
                    >
                      {c.bias}
                    </span>
                    <span className="flex-1 leading-relaxed text-muted-foreground">
                      {c.narrative}
                    </span>
                    <CitationBadges citations={c.citations} />
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* Risk notes */}
          {idea.risk_notes && (
            <section>
              <div className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                Riesgo
              </div>
              <p className="mt-1 text-muted-foreground leading-relaxed">
                {idea.risk_notes}
              </p>
            </section>
          )}
        </CollapsibleContent>
      </Collapsible>
    </Card>
  )
}

interface LevelRowProps {
  label: string
  price: number | null
  rationale: string | null
  citations: ToolCitation[]
}

/** Una row del bloque de niveles: label + precio + rationale truncado. El
 *  rationale completo se muestra en HoverCard al pasar por encima. */
function LevelRow({ label, price, rationale, citations }: LevelRowProps) {
  const priceText =
    price === null
      ? "—"
      : price.toLocaleString(undefined, { minimumFractionDigits: 2 })
  const rowContent = (
    <span className="flex min-w-0 items-baseline gap-2">
      <span className="shrink-0 tabular-nums">{priceText}</span>
      {rationale && (
        <span className="min-w-0 flex-1 truncate text-[11px] text-muted-foreground">
          {rationale}
        </span>
      )}
      <CitationBadges citations={citations} />
    </span>
  )
  return (
    <>
      <span className="text-muted-foreground">{label}</span>
      {rationale ? (
        <HoverCard openDelay={200} closeDelay={80}>
          <HoverCardTrigger asChild>
            <span className="cursor-help">{rowContent}</span>
          </HoverCardTrigger>
          <HoverCardContent className="w-80 p-3">
            <p className="font-mono text-[11px] leading-relaxed">{rationale}</p>
          </HoverCardContent>
        </HoverCard>
      ) : (
        rowContent
      )}
    </>
  )
}
