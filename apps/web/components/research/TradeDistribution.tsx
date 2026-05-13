"use client"

import { useMemo } from "react"
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import { Card, CardContent } from "@/components/ui/card"
import type { TradeDTO } from "@/lib/core/api"

interface TradeDistributionProps {
  trades: TradeDTO[]
}

const BIN_WIDTH = 0.5

/** Histograma de R-multiples + estadísticas de cierre. Usa `trades`
 *  persistidos por `runner.py`. Empty state si el run es anterior a la
 *  migración 007 (trades = []). */
export function TradeDistribution({ trades }: TradeDistributionProps) {
  const stats = useMemo(() => buildStats(trades), [trades])
  const bins = useMemo(() => buildBins(trades), [trades])

  if (trades.length === 0) {
    return (
      <Card className="border-dashed border-border bg-card/20">
        <CardContent className="flex flex-col items-center gap-1 py-8 text-center">
          <span className="eyebrow">distribución de trades</span>
          <p className="text-[13px] text-[var(--fg-2)]">
            Trades individuales no disponibles para este run.
          </p>
          <p className="text-[12px] text-[var(--fg-3)]">
            Re-ejecuta el backtest para verlos.
          </p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card className="border-border bg-card/40">
      <CardContent className="flex flex-col gap-4 p-5">
        <div className="flex items-baseline justify-between">
          <span className="eyebrow">distribución de trades</span>
          <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
            {trades.length} trades · bins de {BIN_WIDTH}R
          </span>
        </div>

        <div className="h-44 w-full">
          <ResponsiveContainer>
            <BarChart
              data={bins}
              margin={{ top: 8, right: 8, bottom: 4, left: 4 }}
            >
              <CartesianGrid
                stroke="var(--color-border)"
                strokeOpacity={0.3}
                vertical={false}
              />
              <XAxis
                dataKey="centerLabel"
                stroke="var(--color-muted-foreground)"
                tick={{ fontSize: 10, fontFamily: "var(--font-mono)" }}
                interval="preserveStartEnd"
              />
              <YAxis
                stroke="var(--color-muted-foreground)"
                tick={{ fontSize: 10, fontFamily: "var(--font-mono)" }}
                allowDecimals={false}
                width={28}
              />
              <Tooltip
                contentStyle={{
                  background: "var(--color-popover)",
                  border: "1px solid var(--color-border)",
                  borderRadius: "0.375rem",
                  fontFamily: "var(--font-mono)",
                  fontSize: "11px",
                }}
                cursor={{ fill: "var(--bg-2)" }}
                formatter={(value, _name, item) => {
                  const range = (item?.payload as Bin | undefined)?.rangeLabel
                  return [`${value} trades`, range ?? "rango"]
                }}
                labelFormatter={() => ""}
              />
              <ReferenceLine
                x="0"
                stroke="var(--color-muted-foreground)"
                strokeDasharray="3 3"
                strokeOpacity={0.5}
              />
              <Bar dataKey="count" radius={[2, 2, 0, 0]}>
                {bins.map((b) => (
                  <Cell
                    key={b.centerLabel}
                    fill={b.center > 0 ? "var(--long)" : "var(--short)"}
                    fillOpacity={0.85}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        <dl className="grid grid-cols-1 gap-x-6 gap-y-1 font-mono text-[12px] tabular-nums sm:grid-cols-2">
          <Row
            label="mejor trade"
            value={`${stats.best.rLabel} · ${stats.best.range}`}
            tone="var(--long)"
          />
          <Row
            label="peor trade"
            value={`${stats.worst.rLabel} · ${stats.worst.range}`}
            tone="var(--short)"
          />
          <Row label="mediana" value={stats.medianLabel} />
          <Row label="salidas" value={stats.exitReasonLabel} />
        </dl>
      </CardContent>
    </Card>
  )
}

interface Bin {
  binMin: number
  binMax: number
  center: number
  centerLabel: string
  rangeLabel: string
  count: number
}

function buildBins(trades: TradeDTO[]): Bin[] {
  if (trades.length === 0) return []
  const rs = trades.map((t) => t.r_multiple)
  const min = Math.min(...rs)
  const max = Math.max(...rs)
  // Anchor bins en 0 para que el reference line caiga limpio entre bins.
  const start = Math.floor(min / BIN_WIDTH) * BIN_WIDTH
  const end = Math.ceil(max / BIN_WIDTH) * BIN_WIDTH
  const bins: Bin[] = []
  for (let lo = start; lo < end + 1e-9; lo += BIN_WIDTH) {
    const hi = lo + BIN_WIDTH
    const center = (lo + hi) / 2
    bins.push({
      binMin: lo,
      binMax: hi,
      center,
      centerLabel: formatR(center),
      rangeLabel: `${formatR(lo)} → ${formatR(hi)}`,
      count: 0,
    })
  }
  for (const r of rs) {
    const idx = Math.min(
      bins.length - 1,
      Math.max(0, Math.floor((r - start) / BIN_WIDTH)),
    )
    bins[idx]!.count += 1
  }
  return bins
}

interface DistStats {
  best: { rLabel: string; range: string }
  worst: { rLabel: string; range: string }
  medianLabel: string
  exitReasonLabel: string
}

function buildStats(trades: TradeDTO[]): DistStats {
  if (trades.length === 0) {
    return {
      best: { rLabel: "—", range: "—" },
      worst: { rLabel: "—", range: "—" },
      medianLabel: "—",
      exitReasonLabel: "—",
    }
  }
  const sorted = [...trades].sort((a, b) => a.r_multiple - b.r_multiple)
  const worst = sorted[0]!
  const best = sorted[sorted.length - 1]!
  const median = sorted[Math.floor(sorted.length / 2)]!
  const bySignal = trades.filter((t) => t.exit_reason === "signal").length
  const byStop = trades.filter((t) => t.exit_reason === "stop").length
  const total = trades.length
  return {
    best: {
      rLabel: `${best.r_multiple >= 0 ? "+" : ""}${best.r_multiple.toFixed(2)}R`,
      range: tradeRange(best),
    },
    worst: {
      rLabel: `${worst.r_multiple >= 0 ? "+" : ""}${worst.r_multiple.toFixed(2)}R`,
      range: tradeRange(worst),
    },
    medianLabel: `${median.r_multiple >= 0 ? "+" : ""}${median.r_multiple.toFixed(2)}R`,
    exitReasonLabel: `${Math.round((bySignal / total) * 100)}% señal · ${Math.round((byStop / total) * 100)}% stop`,
  }
}

function tradeRange(t: TradeDTO): string {
  const a = new Date(t.entry_ts)
  const b = new Date(t.exit_ts)
  const sameDay =
    a.toDateString() === b.toDateString()
  const fmt = (d: Date, withTime: boolean) =>
    withTime
      ? d.toLocaleString(undefined, {
          day: "2-digit",
          month: "short",
          hour: "2-digit",
          minute: "2-digit",
        })
      : d.toLocaleDateString(undefined, {
          day: "2-digit",
          month: "short",
        })
  if (sameDay) {
    return `${fmt(a, true)} → ${b.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}`
  }
  return `${fmt(a, false)} → ${fmt(b, false)}`
}

function formatR(r: number): string {
  if (r === 0) return "0"
  return `${r > 0 ? "+" : ""}${r.toFixed(1)}R`
}

function Row({
  label,
  value,
  tone,
}: {
  label: string
  value: string
  tone?: string
}) {
  return (
    <div className="flex items-baseline justify-between gap-2 border-t border-[color:var(--line-soft)] pt-1 first:border-t-0 first:pt-0 sm:border-t-0 sm:pt-0">
      <span className="text-[var(--fg-3)]">{label}</span>
      <span
        className="text-foreground"
        style={tone ? { color: tone } : undefined}
      >
        {value}
      </span>
    </div>
  )
}
