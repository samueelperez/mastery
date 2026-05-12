"use client"

import { ChevronDownIcon, ChevronRightIcon, ChevronUpIcon } from "lucide-react"
import Link from "next/link"
import { useMemo, useState } from "react"

import { Card } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import type { StrategyWinrateDTO } from "@/lib/core/api"
import { formatSetupTag } from "@/lib/journal/format-setup-tag"
import { cn } from "@/lib/core/utils"

type SortKey = "n_closed" | "win_rate_pct" | "avg_r" | "last_closed_at"

interface StrategyWinrateTableProps {
  rows: StrategyWinrateDTO[]
  loading: boolean
  error?: string
}

export function StrategyWinrateTable({
  rows,
  loading,
  error,
}: StrategyWinrateTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>("n_closed")
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc")

  const sorted = useMemo(() => {
    const compareNum = (a: number | null, b: number | null) => {
      const aN = a ?? Number.NEGATIVE_INFINITY
      const bN = b ?? Number.NEGATIVE_INFINITY
      return aN - bN
    }
    const compareDate = (a: string | null, b: string | null) => {
      const aT = a ? new Date(a).getTime() : 0
      const bT = b ? new Date(b).getTime() : 0
      return aT - bT
    }
    const cmp = (a: StrategyWinrateDTO, b: StrategyWinrateDTO) => {
      switch (sortKey) {
        case "n_closed":
          return a.n_closed - b.n_closed
        case "win_rate_pct":
          return compareNum(a.win_rate_pct, b.win_rate_pct)
        case "avg_r":
          return compareNum(a.avg_r, b.avg_r)
        case "last_closed_at":
          return compareDate(a.last_closed_at, b.last_closed_at)
      }
    }
    const out = [...rows].sort(cmp)
    return sortDir === "desc" ? out.reverse() : out
  }, [rows, sortKey, sortDir])

  if (loading && rows.length === 0) {
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
  if (rows.length === 0) {
    return (
      <Card className="border-dashed border-border bg-card/20 p-6 text-center">
        <p className="eyebrow">aún no hay setups cerrados</p>
        <p className="mt-2 text-xs text-[var(--fg-2)]">
          Pídele al copiloto un trade idea long/short y deja que el watcher
          procese cierres en cada vela. Cuando un setup toque SL o TP,
          aparecerá aquí agrupado por su <code className="font-mono">setup_tag</code>.
        </p>
      </Card>
    )
  }

  const toggleSort = (k: SortKey) => {
    if (sortKey === k) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"))
    } else {
      setSortKey(k)
      setSortDir("desc")
    }
  }

  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <table className="w-full min-w-[42rem] text-xs">
        <thead className="bg-[oklch(0.18_0.018_260)]">
          <tr>
            <Th>estrategia</Th>
            <SortableTh
              label="cerrados"
              k="n_closed"
              sortKey={sortKey}
              sortDir={sortDir}
              onClick={toggleSort}
              align="right"
            />
            <SortableTh
              label="win-rate"
              k="win_rate_pct"
              sortKey={sortKey}
              sortDir={sortDir}
              onClick={toggleSort}
              align="right"
            />
            <SortableTh
              label="avg R"
              k="avg_r"
              sortKey={sortKey}
              sortDir={sortDir}
              onClick={toggleSort}
              align="right"
            />
            <SortableTh
              label="último cierre"
              k="last_closed_at"
              sortKey={sortKey}
              sortDir={sortDir}
              onClick={toggleSort}
              align="right"
            />
          </tr>
        </thead>
        <tbody className="divide-y divide-[color:var(--line-soft)]">
          {sorted.map((r) => (
            <tr
              key={r.setup_tag}
              className="group cursor-pointer transition-colors duration-150 ease-out hover:bg-[var(--bg-2)] focus-within:bg-[var(--bg-2)]"
            >
              <Td>
                <Link
                  href={`/research/strategies/${encodeURIComponent(r.setup_tag)}`}
                  className="inline-flex flex-col items-start gap-0.5 outline-none transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2"
                  aria-label={`ver detalle de ${r.setup_tag}`}
                >
                  <span className="inline-flex items-center gap-1.5 text-[13px] font-medium tracking-tight text-foreground">
                    {formatSetupTag(r.setup_tag)}
                    <ChevronRightIcon
                      className="size-3 text-[var(--fg-3)] transition-transform group-hover:translate-x-0.5 group-hover:text-foreground"
                      aria-hidden
                    />
                  </span>
                  <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
                    {r.setup_tag}
                  </span>
                </Link>
              </Td>
              <Td align="right" mono>
                {r.n_closed} <span className="text-[var(--fg-3)]">({r.n_wins}W)</span>
              </Td>
              <Td align="right" mono>
                <WinRateCell value={r.win_rate_pct} />
              </Td>
              <Td align="right" mono>
                <AvgRCell value={r.avg_r} />
              </Td>
              <Td align="right" mono>
                {short(r.last_closed_at)}
              </Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function SortableTh({
  label,
  k,
  sortKey,
  sortDir,
  onClick,
  align = "left",
}: {
  label: string
  k: SortKey
  sortKey: SortKey
  sortDir: "asc" | "desc"
  onClick: (k: SortKey) => void
  align?: "left" | "right"
}) {
  const active = sortKey === k
  return (
    <th
      className={cn(
        "px-3 py-2 pointer-coarse:py-3 font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--fg-3)] font-medium",
        align === "right" ? "text-right" : "text-left",
      )}
    >
      <button
        type="button"
        onClick={() => onClick(k)}
        className={cn(
          "inline-flex items-center gap-1 transition-colors hover:text-foreground",
          align === "right" && "flex-row-reverse",
          active && "text-foreground",
        )}
      >
        <span>{label}</span>
        {active &&
          (sortDir === "desc" ? (
            <ChevronDownIcon className="size-3" aria-hidden />
          ) : (
            <ChevronUpIcon className="size-3" aria-hidden />
          ))}
      </button>
    </th>
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

function WinRateCell({ value }: { value: number | null }) {
  if (value === null || value === undefined) return <span>—</span>
  const tone =
    value >= 55
      ? "var(--long)"
      : value <= 45
        ? "var(--short)"
        : "var(--fg-2)"
  return <span style={{ color: tone }}>{value.toFixed(1)}%</span>
}

function AvgRCell({ value }: { value: number | null }) {
  if (value === null || value === undefined) return <span>—</span>
  const tone =
    value > 0
      ? "var(--long)"
      : value < 0
        ? "var(--short)"
        : "var(--fg-2)"
  const sign = value >= 0 ? "+" : ""
  return (
    <span style={{ color: tone }}>
      {sign}
      {value.toFixed(2)}R
    </span>
  )
}

function short(ts: string | null): string {
  if (!ts) return "—"
  const d = new Date(ts)
  return `${d.toLocaleDateString(undefined, { month: "short", day: "numeric" })} ${d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false })}`
}
