"use client"

import { Card, CardContent } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import type { BacktestRunDetailDTO } from "@/lib/api"

import { DrawdownChart } from "./DrawdownChart"
import { EquityCurve } from "./EquityCurve"
import { MetricsCard } from "./MetricsCard"

interface BacktestDetailProps {
  run: BacktestRunDetailDTO
}

export function BacktestDetail({ run }: BacktestDetailProps) {
  const initialEquity = Number(run.equity_curve[0]?.[1] ?? 10_000)

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <div className="flex items-baseline gap-3">
          <h1 className="font-mono text-lg tracking-tight text-foreground">
            {run.strategy_id}
          </h1>
          <span className="font-mono text-xs text-muted-foreground">
            {run.symbol} · {run.timeframe} · {fmtRange(run.range_start, run.range_end)}
          </span>
        </div>
        <p className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
          run_id <span className="text-foreground">{run.id}</span> · fees{" "}
          <span className="text-foreground">{run.fees_bps} bps</span> · slippage{" "}
          <span className="text-foreground">{run.slippage_atr} × ATR</span>
        </p>
      </div>

      {run.metrics && <MetricsCard metrics={run.metrics} />}

      {run.equity_curve.length > 0 ? (
        <Card className="border-border/60 bg-card/40">
          <CardContent className="space-y-6 pt-6">
            <EquityCurve curve={run.equity_curve} initialEquity={initialEquity} />
            <Separator />
            <DrawdownChart curve={run.equity_curve} />
          </CardContent>
        </Card>
      ) : (
        <p className="font-mono text-xs text-muted-foreground">
          (no equity curve persisted for this run)
        </p>
      )}

      <Card className="border-border/60 bg-card/40">
        <CardContent className="pt-6">
          <h3 className="mb-2 font-mono text-xs uppercase tracking-widest text-muted-foreground">
            params
          </h3>
          <pre className="overflow-x-auto rounded-md bg-background/40 p-3 font-mono text-xs text-foreground/90">
            {JSON.stringify(run.params, null, 2)}
          </pre>
        </CardContent>
      </Card>
    </div>
  )
}

function fmtRange(a: string, b: string): string {
  const fa = new Date(a).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  })
  const fb = new Date(b).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  })
  return `${fa} → ${fb}`
}
