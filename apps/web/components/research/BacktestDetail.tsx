"use client"

import { useQuery } from "@tanstack/react-query"
import { useMemo } from "react"

import { Card, CardContent } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import {
  fetchStrategyRegistry,
  type BacktestRunDetailDTO,
  type StrategyRegistryDTO,
} from "@/lib/core/api"

import { AdvancedDiagnostics } from "./AdvancedDiagnostics"
import { BacktestVerdict } from "./BacktestVerdict"
import { BehaviorTriad } from "./BehaviorTriad"
import { DrawdownChart } from "./DrawdownChart"
import { EquityCurve } from "./EquityCurve"
import { RobustnessSection } from "./RobustnessSection"
import { StrategyExplainer } from "./StrategyExplainer"
import { TradeDistribution } from "./TradeDistribution"

interface BacktestDetailProps {
  run: BacktestRunDetailDTO
}

export function BacktestDetail({ run }: BacktestDetailProps) {
  const initialEquity = Number(run.equity_curve[0]?.[1] ?? 10_000)

  const registryQuery = useQuery({
    queryKey: ["strategy-registry"],
    queryFn: ({ signal }) => fetchStrategyRegistry({ signal }),
    staleTime: Infinity,
  })

  const registryEntry = useMemo<StrategyRegistryDTO | undefined>(() => {
    return (registryQuery.data ?? []).find((s) => s.id === run.strategy_id)
  }, [registryQuery.data, run.strategy_id])

  const displayName = registryEntry?.name ?? run.strategy_id

  return (
    <div className="flex flex-col gap-5">
      {/* Header — meta minimalista. La narrativa real vive en VerdictHero. */}
      <header className="flex flex-col gap-1">
        <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--fg-3)]">
          backtest run
        </span>
        <h1 className="text-xl font-semibold tracking-tight text-foreground">
          {displayName}{" "}
          <span className="text-[var(--fg-3)]">·</span>{" "}
          <span className="font-mono text-[var(--fg-2)]">
            {run.symbol} {run.timeframe}
          </span>
        </h1>
        <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
          {registryEntry && <span>{run.strategy_id} · </span>}id{" "}
          {run.id.slice(0, 8)}… · ejecutado{" "}
          {new Date(run.created_at).toLocaleDateString(undefined, {
            day: "2-digit",
            month: "short",
            year: "numeric",
          })}
        </p>
      </header>

      {/* 1. Verdict hero */}
      <BacktestVerdict metrics={run.metrics} />

      {/* 2. ¿Qué hace esta estrategia? */}
      <StrategyExplainer run={run} registryEntry={registryEntry} />

      {/* 3. Comportamiento — 3 KPIs lego */}
      <BehaviorTriad run={run} />

      {/* 4. Robustez — DSR/PSR/PBO con copy lego */}
      {run.metrics && <RobustnessSection metrics={run.metrics} />}

      {/* 5. Curvas */}
      {run.equity_curve.length > 0 && (
        <Card className="border-border bg-card/40">
          <CardContent className="flex flex-col gap-4 p-5">
            <span className="eyebrow">comportamiento en el tiempo</span>
            <EquityCurve
              curve={run.equity_curve}
              initialEquity={initialEquity}
            />
            <Separator />
            <DrawdownChart curve={run.equity_curve} />
          </CardContent>
        </Card>
      )}

      {/* 6. Distribución de trades — solo si trades persistidos */}
      <TradeDistribution trades={run.trades} />

      {/* 7. Diagnóstico avanzado (Accordion colapsado) */}
      <AdvancedDiagnostics run={run} />
    </div>
  )
}
