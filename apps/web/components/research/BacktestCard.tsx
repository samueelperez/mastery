"use client"

import Link from "next/link"

import { Card, CardContent } from "@/components/ui/card"
import { verdictOf } from "@/lib/backtest-verdict"
import type { BacktestRunSummaryDTO, StrategyRegistryDTO } from "@/lib/api"

interface BacktestCardProps {
  run: BacktestRunSummaryDTO
  /** Mapa strategy_id → entry del registry, para mostrar la `description`
   *  corta debajo del strategy_id. Si no está, se muestra solo el id. */
  registry?: Record<string, StrategyRegistryDTO>
}

/** Card de un backtest run en la lista. Header: verdict pill + meta. Body:
 *  strategy + description corta. Footer: DSR + expectancy + trades + relativo.
 *
 *  Decisión: NO sparkline aquí. La summary endpoint no incluye `equity_curve`
 *  para mantenerla ligera, y hacer fetch individual por card sería N+1. La
 *  curva vive en el detalle. */
export function BacktestCard({ run, registry }: BacktestCardProps) {
  const verdict = verdictOf(run.metrics)
  const entry = registry?.[run.strategy_id]
  const displayName = entry?.name ?? run.strategy_id
  const description = entry?.description ?? ""
  const m = run.metrics

  const expectancyTone =
    m === null
      ? "var(--fg-2)"
      : m.expectancy_R > 0
        ? "var(--long)"
        : m.expectancy_R < 0
          ? "var(--short)"
          : "var(--fg-2)"
  const ddPct = m ? (m.max_drawdown * 100).toFixed(0) : null
  const ddTone =
    m === null
      ? "var(--fg-2)"
      : m.max_drawdown > 0.3
        ? "var(--short)"
        : "var(--fg-2)"

  return (
    <Link
      href={`/research/backtests/${run.id}`}
      className="block rounded-md outline-none transition-colors focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2"
      aria-label={`abrir backtest ${run.strategy_id}`}
    >
      <Card className="border-border bg-card/40 transition-colors hover:bg-card/80">
        <CardContent className="flex flex-col gap-3 p-4">
          {/* row 1: verdict + meta */}
          <div className="flex items-start justify-between gap-2">
            <span
              className="inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.14em]"
              style={{
                color: verdict.tone,
                backgroundColor: verdict.bg,
                borderColor: verdict.border,
              }}
            >
              {verdict.label}
            </span>
            <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
              {run.symbol} · {run.timeframe}
            </span>
          </div>

          {/* row 2: strategy name (prominente) + id técnico (sub) + descripción */}
          <div className="flex flex-col gap-1">
            <span className="truncate text-sm font-semibold tracking-tight text-foreground">
              {displayName}
            </span>
            {entry && (
              <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
                {run.strategy_id}
              </span>
            )}
            {description && (
              <span className="line-clamp-2 text-[12px] leading-relaxed text-[var(--fg-2)]">
                {description}
              </span>
            )}
          </div>

          {/* row 3: stats key — DSR | expectancy | trades | DD */}
          {m ? (
            <div className="grid grid-cols-2 gap-x-3 gap-y-1 font-mono text-[11px] tabular-nums">
              <Stat label="DSR" value={m.deflated_sharpe.toFixed(2)} />
              <Stat
                label="expectancy"
                value={`${m.expectancy_R >= 0 ? "+" : ""}${m.expectancy_R.toFixed(2)}R`}
                tone={expectancyTone}
              />
              <Stat label="trades" value={String(m.n_trades)} />
              <Stat label="max DD" value={`${ddPct}%`} tone={ddTone} />
            </div>
          ) : (
            <div className="text-[12px] text-[var(--fg-3)]">
              ejecutando…
            </div>
          )}

          {/* row 4: timestamp */}
          <div className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
            creado {formatRelative(run.created_at)}
          </div>
        </CardContent>
      </Card>
    </Link>
  )
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string
  value: string
  tone?: string
}) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-[var(--fg-3)]">{label}</span>
      <span className="text-foreground" style={tone ? { color: tone } : undefined}>
        {value}
      </span>
    </div>
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
