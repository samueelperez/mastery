"use client"

import { useQuery } from "@tanstack/react-query"
import Link from "next/link"

import { BacktestList } from "@/components/research/BacktestList"
import { Card, CardContent } from "@/components/ui/card"
import { cn } from "@/lib/utils"
import {
  fetchBacktests,
  fetchJournalTrades,
  type BacktestRunSummaryDTO,
  type JournalTradeListRowDTO,
} from "@/lib/api"

export default function ResearchOverviewPage() {
  const backtests = useQuery({
    queryKey: ["backtests", { limit: 50 }],
    queryFn: ({ signal }) => fetchBacktests({ limit: 50, signal }),
  })
  const trades = useQuery({
    queryKey: ["journal-trades", { limit: 100 }],
    queryFn: ({ signal }) => fetchJournalTrades({ limit: 100, signal }),
  })

  const hero = computeHero(backtests.data ?? [], trades.data ?? [])

  return (
    <div className="flex flex-col gap-8">
      <Hero stat={hero} loading={backtests.isLoading || trades.isLoading} />

      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="font-mono text-sm uppercase tracking-widest text-foreground">
              recent backtests
            </h2>
            <p className="text-xs text-muted-foreground">
              Latest runs across all strategies.
            </p>
          </div>
          <Link
            href="/research/backtests"
            className="font-mono text-xs text-muted-foreground transition-colors duration-150 hover:text-foreground"
          >
            view all →
          </Link>
        </div>
        <BacktestList
          runs={(backtests.data ?? []).slice(0, 5)}
          loading={backtests.isLoading}
          error={backtests.error?.message}
        />
      </section>
    </div>
  )
}

interface HeroStat {
  headline: string
  caption: string
  tone: "good" | "bad" | "neutral"
  rightPrimary: string
  rightSecondary: string
}

function computeHero(
  runs: BacktestRunSummaryDTO[],
  trades: JournalTradeListRowDTO[],
): HeroStat {
  const closed = trades.filter(
    (t) => t.r_multiple !== null && t.r_multiple !== undefined,
  )
  const wins = closed.filter((t) => (t.r_multiple ?? 0) > 0).length
  const losses = closed.length - wins
  const wr = closed.length > 0 ? wins / closed.length : 0

  const dsrValues = runs
    .filter((r) => r.metrics?.deflated_sharpe !== undefined)
    .map((r) => r.metrics!.deflated_sharpe)
  const bestDsr = dsrValues.length > 0 ? Math.max(...dsrValues) : null
  const passing = runs.filter(
    (r) =>
      r.metrics &&
      !r.metrics.overfit_warning &&
      r.metrics.deflated_sharpe >= 0.5,
  ).length

  if (bestDsr === null && closed.length === 0) {
    return {
      headline: "—",
      caption: "no data yet · run a backtest or import the journal to populate.",
      tone: "neutral",
      rightPrimary: `${runs.length} runs`,
      rightSecondary: `${trades.length} trades`,
    }
  }
  if (bestDsr !== null) {
    return {
      headline: bestDsr.toFixed(2),
      caption: `best DSR across ${runs.length} run${runs.length === 1 ? "" : "s"}; ${passing} pass DSR≥0.5 + no overfit gate.`,
      tone: bestDsr >= 0.95 ? "good" : bestDsr >= 0.5 ? "neutral" : "bad",
      rightPrimary: `${closed.length} trades closed`,
      rightSecondary: `${(wr * 100).toFixed(0)}% WR · ${wins}W ${losses}L`,
    }
  }
  return {
    headline: `${(wr * 100).toFixed(0)}%`,
    caption: `journal win-rate · ${closed.length} closed trade${closed.length === 1 ? "" : "s"}.`,
    tone: wr >= 0.5 ? "good" : "bad",
    rightPrimary: `${runs.length} backtests`,
    rightSecondary: "pending DSR data",
  }
}

function Hero({ stat, loading }: { stat: HeroStat; loading: boolean }) {
  return (
    <Card className="border-border bg-card">
      <CardContent className="flex flex-col gap-6 py-6 sm:flex-row sm:items-end sm:justify-between">
        <div className="flex flex-col gap-1.5">
          <span className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
            edge so far
          </span>
          <span
            className={cn(
              "font-mono text-5xl font-medium tabular-nums",
              loading && "opacity-40",
              stat.tone === "good"
                ? "text-primary"
                : stat.tone === "bad"
                  ? "text-destructive"
                  : "text-foreground",
            )}
          >
            {stat.headline}
          </span>
          <p className="max-w-md text-xs text-muted-foreground">{stat.caption}</p>
        </div>
        <div className="flex flex-col items-start gap-0.5 sm:items-end">
          <span className="font-mono text-sm tabular-nums text-foreground">
            {stat.rightPrimary}
          </span>
          <span className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
            {stat.rightSecondary}
          </span>
        </div>
      </CardContent>
    </Card>
  )
}
