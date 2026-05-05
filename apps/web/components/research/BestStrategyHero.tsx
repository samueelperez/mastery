"use client"

import { ArrowRightIcon, TrophyIcon } from "lucide-react"
import Link from "next/link"

import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { formatSetupTag } from "@/lib/format-setup-tag"
import type { RankedStrategy } from "@/lib/strategy-rank"

interface BestStrategyHeroProps {
  strategy: RankedStrategy | null
  /** Para distinguir el empty state "todavía no hay datos" del "ninguna
   *  cumple el min N". Si total > 0 pero strategy === null, sabemos que el
   *  hero no se llena porque ninguna alcanza el umbral. */
  totalStrategies: number
}

/** Hero "mejor estrategia" en `/research/strategies`. Card con tono cálido
 *  (long tinted) destacando el #1 según el score compuesto que pondera N.
 *
 *  Empty states:
 *  - 0 strategies en el sistema → invita a empezar.
 *  - >0 pero ninguna ≥5 cierres → explica el umbral. */
export function BestStrategyHero({
  strategy,
  totalStrategies,
}: BestStrategyHeroProps) {
  if (strategy === null) {
    return (
      <Card className="border-dashed border-border bg-card/20">
        <CardContent className="flex flex-col items-start gap-1 p-5">
          <span className="eyebrow flex items-center gap-2">
            <TrophyIcon
              className="size-3.5 text-[var(--fg-3)]"
              aria-hidden
            />
            mejor estrategia
          </span>
          {totalStrategies === 0 ? (
            <>
              <p className="text-[14px] text-foreground">
                Aún no hay estrategias con trades cerrados.
              </p>
              <p className="text-[13px] text-muted-foreground">
                Pídele al copiloto un trade idea o importa un CSV. Cuando
                acumules cierres, la estrategia con mejor ratio aparecerá
                aquí destacada.
              </p>
            </>
          ) : (
            <>
              <p className="text-[14px] text-foreground">
                Aún no hay una estrategia con muestra suficiente.
              </p>
              <p className="text-[13px] text-muted-foreground">
                Para evitar premiar la suerte, requerimos al menos 5 trades
                cerrados. Cuando alguna alcance ese umbral, aparecerá aquí
                destacada como la mejor.
              </p>
            </>
          )}
        </CardContent>
      </Card>
    )
  }

  const avgR = strategy.avg_r ?? 0
  const winRate = strategy.win_rate_pct ?? 0
  const winRateTone =
    winRate >= 55
      ? "var(--long)"
      : winRate <= 45
        ? "var(--short)"
        : "var(--fg-2)"
  const avgTone =
    avgR > 0
      ? "var(--long)"
      : avgR < 0
        ? "var(--short)"
        : "var(--fg-2)"

  return (
    <Card
      className="border bg-[color:var(--long)]/[0.04]"
      style={{
        borderColor: "color-mix(in oklch, var(--long) 30%, transparent)",
      }}
    >
      <CardContent className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-col gap-2">
          <span className="eyebrow flex items-center gap-2">
            <TrophyIcon
              className="size-3.5"
              style={{ color: "var(--long)" }}
              aria-hidden
            />
            mejor estrategia · score {strategy.score.toFixed(2)}
          </span>
          <div className="flex flex-col gap-0.5">
            <h2 className="text-2xl font-semibold tracking-tight text-foreground">
              {formatSetupTag(strategy.setup_tag)}
            </h2>
            <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
              {strategy.setup_tag}
            </span>
          </div>
          <p className="font-mono text-[13px] tabular-nums">
            <span className="text-foreground">
              {strategy.n_closed} {strategy.n_closed === 1 ? "trade" : "trades"} cerrados
            </span>
            <span className="text-[var(--fg-3)]"> · </span>
            <span style={{ color: winRateTone }}>
              {winRate.toFixed(0)}% acierto
            </span>
            <span className="text-[var(--fg-3)]"> · </span>
            <span style={{ color: avgTone }}>
              {avgR >= 0 ? "+" : ""}
              {avgR.toFixed(2)}R promedio
            </span>
          </p>
          {strategy.last_closed_at && (
            <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--fg-3)]">
              último cierre {formatRelative(strategy.last_closed_at)}
            </p>
          )}
        </div>

        <Button
          asChild
          variant="outline"
          className="self-start sm:self-center"
        >
          <Link
            href={`/research/strategies/${encodeURIComponent(strategy.setup_tag)}`}
            aria-label={`Ver detalle completo de ${strategy.setup_tag}`}
          >
            Ver detalle completo
            <ArrowRightIcon className="size-4" aria-hidden />
          </Link>
        </Button>
      </CardContent>
    </Card>
  )
}

function formatRelative(iso: string): string {
  const dt = new Date(iso)
  const diffMs = Date.now() - dt.getTime()
  const min = Math.round(diffMs / 60_000)
  if (min < 1) return "ahora"
  if (min < 60) return `hace ${min}m`
  const h = Math.round(min / 60)
  if (h < 24) return `hace ${h}h`
  const d = Math.round(h / 24)
  return `hace ${d}d`
}
