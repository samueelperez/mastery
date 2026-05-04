"use client"

import { AlertTriangleIcon, CheckIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { cn } from "@/lib/utils"
import type { StrategyMetricsDTO } from "@/lib/api"

interface MetricsCardProps {
  metrics: StrategyMetricsDTO
}

export function MetricsCard({ metrics: m }: MetricsCardProps) {
  return (
    <Card className="border-border bg-card">
      <CardContent className="space-y-4 pt-6">
        <div className="flex items-center justify-between">
          <div className="flex flex-col">
            <span className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
              deflated sharpe (López de Prado)
            </span>
            <span
              className={cn(
                "font-mono text-3xl font-medium tabular-nums",
                m.deflated_sharpe >= 0.95
                  ? "text-primary"
                  : m.deflated_sharpe >= 0.5
                    ? "text-foreground"
                    : "text-destructive",
              )}
            >
              {m.deflated_sharpe.toFixed(3)}
            </span>
          </div>
          {m.overfit_warning ? (
            <Badge variant="destructive" className="gap-1">
              <AlertTriangleIcon className="size-3" />
              overfit_warning
            </Badge>
          ) : (
            <Badge variant="secondary" className="gap-1">
              <CheckIcon className="size-3" />
              passes DSR/DD gates
            </Badge>
          )}
        </div>

        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat label="sharpe" value={m.sharpe.toFixed(2)} />
          <Stat
            label="sortino"
            value={m.sortino === null ? "—" : m.sortino.toFixed(2)}
            title={
              m.sortino === null
                ? "Undefined: no losing returns in the equity curve."
                : undefined
            }
          />
          <Stat label="PSR" value={m.probabilistic_sharpe.toFixed(2)} />
          <Stat
            label="PBO (exp.)"
            value={
              m.probability_of_overfit !== null
                ? m.probability_of_overfit.toFixed(2)
                : "—"
            }
            title="Experimental: current CPCV runs the strategy once and ranks fold sub-samples; this is a proxy for true López de Prado PBO. Don't trust the absolute value yet."
          />
          <Stat
            label="max DD"
            value={`${(m.max_drawdown * 100).toFixed(1)}%`}
            danger={m.max_drawdown > 0.3}
          />
          <Stat label="calmar" value={m.calmar.toFixed(2)} />
          <Stat label="ulcer" value={m.ulcer_index.toFixed(2)} />
          <Stat
            label="expectancy R"
            value={m.expectancy_R.toFixed(2)}
            danger={m.expectancy_R < 0}
          />
          <Stat label="win rate" value={`${(m.win_rate * 100).toFixed(0)}%`} />
          <Stat label="avg win" value={`+${m.avg_win_R.toFixed(2)}R`} />
          <Stat label="avg loss" value={`${m.avg_loss_R.toFixed(2)}R`} />
          <Stat label="trades" value={m.n_trades.toString()} />
        </div>
      </CardContent>
    </Card>
  )
}

function Stat({
  label,
  value,
  danger,
  title,
}: {
  label: string
  value: string
  danger?: boolean
  title?: string
}) {
  return (
    <div className="flex flex-col gap-0.5" title={title}>
      <span className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
        {label}
      </span>
      <span
        className={cn(
          "font-mono text-base font-medium tabular-nums",
          danger ? "text-destructive" : "text-foreground",
        )}
      >
        {value}
      </span>
    </div>
  )
}
