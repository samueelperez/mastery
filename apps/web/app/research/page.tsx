"use client"

import { useQuery } from "@tanstack/react-query"
import Link from "next/link"

import { BacktestList } from "@/components/research/BacktestList"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { fetchBacktests, fetchJournalTrades } from "@/lib/api"

export default function ResearchOverviewPage() {
  const backtests = useQuery({
    queryKey: ["backtests", { limit: 5 }],
    queryFn: ({ signal }) => fetchBacktests({ limit: 5, signal }),
  })
  const trades = useQuery({
    queryKey: ["journal-trades", { limit: 5 }],
    queryFn: ({ signal }) => fetchJournalTrades({ limit: 5, signal }),
  })

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-mono text-sm uppercase tracking-widest text-foreground">
            recent backtests
          </h2>
          <p className="text-xs text-muted-foreground">
            Latest 5 runs across all strategies.
          </p>
        </div>
        <Link
          href="/research/backtests"
          className="font-mono text-xs text-muted-foreground hover:text-foreground"
        >
          view all →
        </Link>
      </div>
      <BacktestList
        runs={backtests.data ?? []}
        loading={backtests.isLoading}
        error={backtests.error?.message}
      />

      <div className="flex items-center justify-between pt-4">
        <div>
          <h2 className="font-mono text-sm uppercase tracking-widest text-foreground">
            journal snapshot
          </h2>
          <p className="text-xs text-muted-foreground">
            {trades.data?.length ?? 0} of latest trades · cited by `get_similar_past_trades`.
          </p>
        </div>
        <Link
          href="/research/journal"
          className="font-mono text-xs text-muted-foreground hover:text-foreground"
        >
          view all →
        </Link>
      </div>
      <Card className="border-border/60 bg-card/40">
        <CardHeader>
          <CardTitle className="font-mono text-xs uppercase tracking-widest text-muted-foreground">
            counts
          </CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-6 sm:grid-cols-4">
          <Stat label="trades" value={trades.data?.length ?? 0} />
          <Stat
            label="winners"
            value={
              trades.data?.filter((t) => (t.r_multiple ?? 0) > 0).length ?? 0
            }
          />
          <Stat
            label="losers"
            value={
              trades.data?.filter((t) => (t.r_multiple ?? 0) < 0).length ?? 0
            }
          />
          <Stat
            label="open"
            value={trades.data?.filter((t) => t.exit_px === null).length ?? 0}
          />
        </CardContent>
      </Card>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
        {label}
      </span>
      <span className="font-mono text-2xl font-medium tabular-nums text-foreground">
        {value}
      </span>
    </div>
  )
}
