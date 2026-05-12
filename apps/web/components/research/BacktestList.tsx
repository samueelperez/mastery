"use client"

import { useQueryClient } from "@tanstack/react-query"
import Link from "next/link"
import { AlertTriangleIcon } from "lucide-react"

import { Card } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/core/utils"
import { fetchBacktest, type BacktestRunSummaryDTO } from "@/lib/core/api"

interface BacktestListProps {
  runs: BacktestRunSummaryDTO[]
  loading: boolean
  error?: string
}

export function BacktestList({ runs, loading, error }: BacktestListProps) {
  const queryClient = useQueryClient()
  // Prefetch del detail cuando el usuario pasa el mouse por la fila — al
  // click la transición a /research/backtests/[id] es instantánea porque
  // los datos ya están en cache. El prefetch tiene staleTime largo para
  // que un hover seguido de click no dispare 2 fetches.
  const prefetchDetail = (id: string) => {
    void queryClient.prefetchQuery({
      queryKey: ["backtest", id],
      queryFn: ({ signal }) => fetchBacktest(id, { signal }),
      staleTime: 60_000,
    })
  }

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
      <Card className="border-dashed border-border bg-card/20 p-6 text-center">
        <p className="eyebrow">aún no hay backtests</p>
        <p className="mt-2 text-xs text-[var(--fg-2)]">
          Pídele al copiloto:{" "}
          <span className="font-mono text-foreground">
            &ldquo;haz backtest de ema_cross 21/55 BTCUSDT 4h&rdquo;
          </span>
        </p>
      </Card>
    )
  }

  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <table className="w-full min-w-[44rem] text-xs">
        <thead className="bg-[oklch(0.18_0.018_260)]">
          <tr>
            <Th>estrategia</Th>
            <Th>símbolo</Th>
            <Th>tf</Th>
            <Th align="right">sharpe</Th>
            <Th align="right">DSR</Th>
            <Th align="right">max DD</Th>
            <Th align="right">trades</Th>
            <Th align="right">creado</Th>
            <Th>estado</Th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[color:var(--line-soft)]">
          {runs.map((r) => (
            <tr
              key={r.id}
              onMouseEnter={() => prefetchDetail(r.id)}
              onFocus={() => prefetchDetail(r.id)}
              className="relative transition-colors duration-150 ease-out hover:bg-[var(--bg-2)] focus-within:bg-[var(--violet-soft)]"
            >
              <Td>
                <Link
                  href={`/research/backtests/${r.id}`}
                  className="font-mono text-foreground after:absolute after:inset-0 after:content-[''] hover:text-[var(--violet)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2"
                >
                  {r.strategy_id}
                </Link>
              </Td>
              <Td>{r.symbol}</Td>
              <Td>{r.timeframe}</Td>
              <Td align="right" mono>
                {fmt(r.metrics?.sharpe)}
              </Td>
              <Td align="right" mono>
                <DsrCell value={r.metrics?.deflated_sharpe ?? null} />
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
                  <span className="pill-status pill-status-warn">
                    <AlertTriangleIcon className="size-3" />
                    overfit
                  </span>
                ) : (
                  <span className="pill-status pill-status-ok">ok</span>
                )}
              </Td>
            </tr>
          ))}
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
      className={cn(
        "px-3 py-2 pointer-coarse:py-3 font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--fg-3)] font-medium",
        align === "right" ? "text-right" : "text-left",
      )}
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
      className={cn(
        "px-3 py-2 pointer-coarse:py-4",
        align === "right" ? "text-right" : "text-left",
        mono && "font-mono tabular-nums",
      )}
    >
      {children}
    </td>
  )
}

/** Cell DSR con barra mini (60×4px) que escala 0..1.5 → 0..100%. */
function DsrCell({ value }: { value: number | null }) {
  if (value === null || value === undefined) return <span>—</span>
  const pctFill = Math.max(0, Math.min(1, value / 1.5)) * 100
  const tone =
    value >= 0.95
      ? "var(--amber)"
      : value >= 0.5
        ? "var(--violet)"
        : "var(--fg-3)"
  return (
    <span className="inline-flex items-center justify-end gap-2">
      <span
        aria-hidden
        className="hidden h-1 w-[60px] overflow-hidden rounded-sm bg-[var(--bg-3)] sm:inline-block"
      >
        <span
          className="block h-full rounded-sm"
          style={{ width: `${pctFill}%`, background: tone }}
        />
      </span>
      <span style={{ color: tone }}>{value.toFixed(2)}</span>
    </span>
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
