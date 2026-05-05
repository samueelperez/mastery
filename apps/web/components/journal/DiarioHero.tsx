"use client"

import { FlameIcon, MinusIcon, TrendingDownIcon, TrendingUpIcon } from "lucide-react"

import { Card, CardContent } from "@/components/ui/card"
import type { Streak, WeekStats } from "@/lib/journal-rollup"
import { cn } from "@/lib/utils"

interface DiarioHeroProps {
  weekStats: WeekStats
  streak: Streak
}

/** Hero del Diario unificado. Resume la semana en una frase legible y
 *  muestra la racha actual al lado.
 *
 *  Diseño:
 *  - Eyebrow "esta semana" en mono.
 *  - Big number row sans semibold: "X aciertos · Y fallos · ±Z R".
 *  - Win rate bar segmentada (long verde / short rojo) proporcional.
 *  - Sub-row con racha actual a la derecha. */
export function DiarioHero({ weekStats, streak }: DiarioHeroProps) {
  const { wins, losses, totalR, winRatePct, total } = weekStats

  if (total === 0) {
    return (
      <Card className="border-dashed border-border bg-card/30">
        <CardContent className="flex flex-col items-start gap-1.5 p-5">
          <span className="eyebrow">esta semana</span>
          <p className="text-[15px] text-foreground">
            Aún no hay cierres en los últimos 7 días.
          </p>
          <p className="text-[13px] text-muted-foreground">
            Pídele al copiloto un trade idea o importa un CSV de trades
            cerrados para ver tu rollup aquí.
          </p>
          <StreakRow streak={streak} className="mt-2" />
        </CardContent>
      </Card>
    )
  }

  const totalRTone =
    totalR > 0
      ? "var(--long)"
      : totalR < 0
        ? "var(--short)"
        : "var(--fg-2)"
  const winsPct = total > 0 ? (wins / total) * 100 : 0
  const lossesPct = total > 0 ? (losses / total) * 100 : 0

  return (
    <Card className="border-border bg-card/40">
      <CardContent className="flex flex-col gap-4 p-5">
        <span className="eyebrow">esta semana</span>

        <p className="text-2xl font-semibold leading-tight tracking-tight text-foreground">
          <span style={{ color: "var(--long)" }}>
            <span className="font-mono tabular-nums">{wins}</span>{" "}
            {wins === 1 ? "acierto" : "aciertos"}
          </span>
          <span className="text-[var(--fg-3)]"> · </span>
          <span style={{ color: "var(--short)" }}>
            <span className="font-mono tabular-nums">{losses}</span>{" "}
            {losses === 1 ? "fallo" : "fallos"}
          </span>
          <span className="text-[var(--fg-3)]"> · </span>
          <span
            className="font-mono tabular-nums"
            style={{ color: totalRTone }}
          >
            {totalR >= 0 ? "+" : ""}
            {totalR.toFixed(2)}R
          </span>
        </p>

        {/* Win rate bar segmentada */}
        <div className="flex flex-col gap-1.5">
          <div className="flex h-2 w-full overflow-hidden rounded-full bg-[var(--bg-3)]">
            <span
              className="h-full"
              style={{
                width: `${winsPct}%`,
                backgroundColor: "var(--long)",
              }}
              aria-hidden
            />
            <span
              className="h-full"
              style={{
                width: `${lossesPct}%`,
                backgroundColor: "var(--short)",
              }}
              aria-hidden
            />
          </div>
          <div className="flex items-baseline justify-between font-mono text-[11px] tabular-nums">
            <span className="text-[var(--fg-3)]">
              {winRatePct !== null ? `${winRatePct.toFixed(0)}% acierto` : "—"}
            </span>
            <span className="text-[var(--fg-3)]">
              {total} {total === 1 ? "trade" : "trades"} cerrados
            </span>
          </div>
        </div>

        <StreakRow streak={streak} />
      </CardContent>
    </Card>
  )
}

function StreakRow({
  streak,
  className,
}: {
  streak: Streak
  className?: string
}) {
  if (streak.kind === "none" || streak.length === 0) {
    return (
      <div
        className={cn(
          "flex items-center gap-2 text-[12px] text-[var(--fg-3)]",
          className,
        )}
      >
        <MinusIcon className="size-3.5" aria-hidden />
        Sin racha activa
      </div>
    )
  }
  const tone =
    streak.kind === "win" ? "var(--long)" : "var(--short)"
  const Icon =
    streak.kind === "win"
      ? streak.length >= 3
        ? FlameIcon
        : TrendingUpIcon
      : TrendingDownIcon
  const label =
    streak.kind === "win"
      ? `Racha ganadora · ${streak.length}W consecutivos`
      : `Racha perdedora · ${streak.length}L consecutivos`
  return (
    <div
      className={cn(
        "flex items-center gap-2 text-[13px] font-medium",
        className,
      )}
      style={{ color: tone }}
    >
      <Icon className="size-4" aria-hidden />
      {label}
    </div>
  )
}
