"use client"

import { useRouter } from "next/navigation"
import { AlertTriangleIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Card } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import type { BacktestRunSummaryDTO } from "@/lib/api"

interface BacktestListProps {
  runs: BacktestRunSummaryDTO[]
  loading: boolean
  error?: string
}

export function BacktestList({ runs, loading, error }: BacktestListProps) {
  const router = useRouter()
  if (loading && runs.length === 0) {
    return (
      <div className="flex flex-col gap-2">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
    )
  }
  if (error) {
    return <p className="text-xs text-destructive">{error}</p>
  }
  if (runs.length === 0) {
    return (
      <Card className="border-dashed border-border/40 bg-card/20 p-6 text-center">
        <p className="font-mono text-xs uppercase tracking-widest text-muted-foreground">
          no backtests yet
        </p>
        <p className="mt-2 text-xs text-muted-foreground">
          Pídele al copiloto:{" "}
          <span className="font-mono text-foreground">
            &ldquo;haz backtest de ema_cross 21/55 BTCUSDT 4h&rdquo;
          </span>
        </p>
      </Card>
    )
  }

  return (
    <div className="overflow-hidden rounded-md border border-border/40">
      <table className="w-full text-xs">
        <thead className="bg-card/40 text-[10px] uppercase tracking-widest text-muted-foreground">
          <tr>
            <Th>strategy</Th>
            <Th>symbol</Th>
            <Th>tf</Th>
            <Th align="right">sharpe</Th>
            <Th align="right">DSR</Th>
            <Th align="right">max DD</Th>
            <Th align="right">trades</Th>
            <Th align="right">created</Th>
            <Th>flags</Th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/30">
          {runs.map((r) => {
            const navigate = () => router.push(`/research/backtests/${r.id}`)
            return (
              <tr
                key={r.id}
                tabIndex={0}
                role="link"
                aria-label={`open ${r.strategy_id} ${r.symbol} ${r.timeframe} backtest`}
                onClick={navigate}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault()
                    navigate()
                  }
                }}
                className="cursor-pointer transition-colors duration-150 ease-out hover:bg-accent/10 hover:text-primary focus-visible:bg-accent/15 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:-outline-offset-2"
              >
                <Td>
                  <span className="font-mono text-foreground">{r.strategy_id}</span>
                </Td>
                <Td>{r.symbol}</Td>
                <Td>{r.timeframe}</Td>
                <Td align="right" mono>
                  {fmt(r.metrics?.sharpe)}
                </Td>
                <Td align="right" mono>
                  {fmt(r.metrics?.deflated_sharpe)}
                </Td>
                <Td align="right" mono>
                  {pct(r.metrics?.max_drawdown)}
                </Td>
                <Td align="right" mono>
                  {r.metrics?.n_trades ?? "—"}
                </Td>
                <Td align="right" mono>
                  {short(r.created_at)}
                </Td>
                <Td>
                  {r.metrics?.overfit_warning ? (
                    <Badge variant="destructive" className="gap-1">
                      <AlertTriangleIcon className="size-3" />
                      overfit
                    </Badge>
                  ) : (
                    <Badge variant="secondary">ok</Badge>
                  )}
                </Td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function Th({
  children,
  align = "left",
}: {
  children: React.ReactNode
  align?: "left" | "right"
}) {
  return (
    <th
      className={`px-3 py-2 font-medium ${align === "right" ? "text-right" : "text-left"}`}
    >
      {children}
    </th>
  )
}

function Td({
  children,
  align = "left",
  mono,
}: {
  children: React.ReactNode
  align?: "left" | "right"
  mono?: boolean
}) {
  return (
    <td
      className={`px-3 py-2 ${align === "right" ? "text-right" : "text-left"} ${mono ? "font-mono tabular-nums" : ""}`}
    >
      {children}
    </td>
  )
}

function fmt(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—"
  return n.toFixed(2)
}

function pct(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—"
  return `${(n * 100).toFixed(1)}%`
}

function short(ts: string): string {
  const d = new Date(ts)
  return `${d.toLocaleDateString(undefined, { month: "short", day: "numeric" })} ${d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false })}`
}
