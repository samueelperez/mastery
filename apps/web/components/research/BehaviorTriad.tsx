"use client"

import {
  ActivityIcon,
  ShieldIcon,
  TrendingUpIcon,
} from "lucide-react"

import { Card, CardContent } from "@/components/ui/card"
import type { BacktestRunDetailDTO } from "@/lib/core/api"
import { cn } from "@/lib/core/utils"

interface BehaviorTriadProps {
  run: BacktestRunDetailDTO
}

/** Tres KPIs de cómo se comportó la estrategia, en lenguaje directo:
 *  rentabilidad / riesgo / frecuencia. Cada uno tiene número grande +
 *  descripción 1-line + dato auxiliar. */
export function BehaviorTriad({ run }: BehaviorTriadProps) {
  const m = run.metrics
  if (!m) {
    return (
      <Card className="border-dashed border-border bg-card/20">
        <CardContent className="py-6 text-center text-[12px] text-[var(--fg-3)]">
          aún sin métricas — el run no ha terminado.
        </CardContent>
      </Card>
    )
  }

  // Retorno total a partir de la equity_curve (si está disponible).
  const curve = run.equity_curve
  const totalReturnPct = (() => {
    if (!curve || curve.length < 2) return null
    const first = curve[0]?.[1] ?? 0
    const last = curve[curve.length - 1]?.[1] ?? first
    if (first <= 0) return null
    return ((last - first) / first) * 100
  })()

  const ddBars = m.max_drawdown_duration_bars
  const ddDays = ddBars > 0 ? barsToDays(ddBars, run.timeframe) : null

  const spanDays = (() => {
    const a = new Date(run.range_start).getTime()
    const b = new Date(run.range_end).getTime()
    return Math.max(1, Math.round((b - a) / (1000 * 60 * 60 * 24)))
  })()
  const tradesPerMonth = (m.n_trades / spanDays) * 30
  const daysBetweenTrades = m.n_trades > 0 ? spanDays / m.n_trades : null

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      <BehaviorCard
        icon={TrendingUpIcon}
        label="rentabilidad"
        primary={
          totalReturnPct !== null
            ? `${totalReturnPct >= 0 ? "+" : ""}${totalReturnPct.toFixed(0)}%`
            : "—"
        }
        primaryTone={
          totalReturnPct === null
            ? "var(--fg-2)"
            : totalReturnPct > 0
              ? "var(--long)"
              : "var(--short)"
        }
        primaryAux="retorno total"
        secondary={`${m.expectancy_R >= 0 ? "+" : ""}${m.expectancy_R.toFixed(2)}R por trade promedio`}
      />
      <BehaviorCard
        icon={ShieldIcon}
        label="riesgo"
        primary={`−${(m.max_drawdown * 100).toFixed(0)}%`}
        primaryTone="var(--short)"
        primaryAux="peor caída"
        secondary={
          ddDays !== null
            ? `recuperó en ${ddDays} día${ddDays === 1 ? "" : "s"} (${ddBars} bars)`
            : "duración del DD desconocida"
        }
      />
      <BehaviorCard
        icon={ActivityIcon}
        label="frecuencia"
        primary={String(m.n_trades)}
        primaryTone="var(--foreground)"
        primaryAux={`trades en ${spanDays} día${spanDays === 1 ? "" : "s"}`}
        secondary={
          daysBetweenTrades !== null
            ? `~${tradesPerMonth.toFixed(1)}/mes · 1 cada ${daysBetweenTrades.toFixed(1)} días`
            : "sin trades suficientes"
        }
      />
    </div>
  )
}

interface BehaviorCardProps {
  icon: React.ComponentType<{ className?: string }>
  label: string
  primary: string
  primaryTone: string
  primaryAux: string
  secondary: string
}

function BehaviorCard({
  icon: Icon,
  label,
  primary,
  primaryTone,
  primaryAux,
  secondary,
}: BehaviorCardProps) {
  return (
    <Card className="border-border bg-card/40">
      <CardContent className="flex flex-col gap-2 p-4">
        <div className="flex items-center justify-between">
          <span className="eyebrow">{label}</span>
          <Icon className="size-3.5 text-[var(--fg-3)]" aria-hidden />
        </div>
        <div className="flex flex-col gap-0.5">
          <span
            className={cn(
              "font-mono text-3xl font-medium tabular-nums leading-none tracking-tight",
            )}
            style={{ color: primaryTone }}
          >
            {primary}
          </span>
          <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--fg-3)]">
            {primaryAux}
          </span>
        </div>
        <p className="text-[12px] leading-relaxed text-[var(--fg-2)]">
          {secondary}
        </p>
      </CardContent>
    </Card>
  )
}

function barsToDays(bars: number, timeframe: string): number {
  // Crypto trades 24/7 → conversión directa de bars × minutos / 1440.
  const minPerBar = (
    {
      "1m": 1,
      "5m": 5,
      "15m": 15,
      "30m": 30,
      "1h": 60,
      "2h": 120,
      "4h": 240,
      "1d": 1440,
    } as Record<string, number>
  )[timeframe]
  if (!minPerBar) return Math.round(bars / 24) // fallback
  return Math.max(1, Math.round((bars * minPerBar) / 1440))
}
