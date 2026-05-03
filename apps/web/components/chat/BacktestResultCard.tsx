"use client"

import Link from "next/link"
import { AlertTriangleIcon, ArrowUpRightIcon, CheckIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { cn } from "@/lib/utils"

interface BacktestToolOutput {
  run_id: string
  strategy_id: string
  n_trades: number
  sharpe: number
  sortino: number
  deflated_sharpe: number
  probabilistic_sharpe: number
  max_drawdown: number
  max_drawdown_duration_bars: number
  calmar: number
  ulcer_index: number
  expectancy_R: number
  win_rate: number
  overfit_warning: boolean
}

interface BacktestResultCardProps {
  output: BacktestToolOutput
  /** Optional input echo for the symbol/tf header. */
  input?: { symbol?: string; timeframe?: string }
}

export function BacktestResultCard({ output: o, input }: BacktestResultCardProps) {
  return (
    <Card className="border-border bg-card">
      <CardHeader className="space-y-1 pb-3">
        <div className="flex items-baseline justify-between gap-3">
          <span className="font-mono text-sm tracking-tight text-foreground">
            backtest · {o.strategy_id}
          </span>
          {o.overfit_warning ? (
            <Badge variant="destructive" className="gap-1">
              <AlertTriangleIcon className="size-3" />
              overfit_warning
            </Badge>
          ) : (
            <Badge variant="secondary" className="gap-1">
              <CheckIcon className="size-3" />
              ok
            </Badge>
          )}
        </div>
        <p className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
          {input?.symbol ?? "—"} · {input?.timeframe ?? "—"} · {o.n_trades} trades · run_id{" "}
          <span className="text-foreground">{o.run_id.slice(0, 8)}</span>
        </p>
      </CardHeader>
      <CardContent className="space-y-3 pb-4">
        <div className="grid grid-cols-3 gap-3">
          <Stat
            label="DSR"
            value={o.deflated_sharpe.toFixed(2)}
            highlight={
              o.deflated_sharpe >= 0.95
                ? "good"
                : o.deflated_sharpe >= 0.5
                  ? "neutral"
                  : "bad"
            }
          />
          <Stat
            label="max DD"
            value={`${(o.max_drawdown * 100).toFixed(1)}%`}
            highlight={o.max_drawdown > 0.3 ? "bad" : "neutral"}
          />
          <Stat
            label="expectancy"
            value={`${o.expectancy_R.toFixed(2)}R`}
            highlight={o.expectancy_R < 0 ? "bad" : "good"}
          />
        </div>
        <Link
          href={`/research/backtests/${o.run_id}`}
          className="group flex items-center gap-1 font-mono text-[11px] uppercase tracking-widest text-muted-foreground hover:text-foreground"
        >
          full metrics + equity curve
          <ArrowUpRightIcon className="size-3 transition-transform group-hover:translate-x-0.5" />
        </Link>
      </CardContent>
    </Card>
  )
}

function Stat({
  label,
  value,
  highlight,
}: {
  label: string
  value: string
  highlight?: "good" | "bad" | "neutral"
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
        {label}
      </span>
      <span
        className={cn(
          "font-mono text-base font-medium tabular-nums",
          highlight === "good"
            ? "text-primary"
            : highlight === "bad"
              ? "text-destructive"
              : "text-foreground",
        )}
      >
        {value}
      </span>
    </div>
  )
}

export type { BacktestToolOutput }
