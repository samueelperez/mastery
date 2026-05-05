"use client"

import { useMemo } from "react"
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

interface StrategyRCurvePoint {
  /** index 1..N en orden cronológico de cierre. */
  n: number
  /** suma acumulada de R-multiples hasta este trade (incluido). */
  cumR: number
  /** R individual de este trade — usado en el tooltip. */
  r: number
  /** símbolo del trade — usado en el tooltip. */
  symbol: string
  /** lado long/short — usado en el tooltip. */
  side: "long" | "short"
  /** ISO closed_at — usado en el tooltip. */
  closedAt: string | null
}

interface StrategyRCurveProps {
  trades: {
    id: string
    symbol: string
    side: "long" | "short"
    r_multiple: number | null
    closed_at: string | null
  }[]
}

/** Curva acumulada de R-multiple por trade cerrado en orden cronológico.
 *  Una sola gráfica que cuenta toda la historia de la estrategia: ¿gana
 *  dinero netamente o no?
 *
 *  El reference line en y=0 hace inmediato distinguir verde sostenido vs
 *  zigzag por encima/debajo de breakeven. */
export function StrategyRCurve({ trades }: StrategyRCurveProps) {
  const data = useMemo<StrategyRCurvePoint[]>(() => {
    const sorted = [...trades]
      .filter((t) => t.r_multiple !== null && t.closed_at !== null)
      .sort((a, b) => {
        const ta = a.closed_at ? Date.parse(a.closed_at) : 0
        const tb = b.closed_at ? Date.parse(b.closed_at) : 0
        return ta - tb
      })
    let acc = 0
    return sorted.map((t, i) => {
      const r = t.r_multiple ?? 0
      acc += r
      return {
        n: i + 1,
        cumR: acc,
        r,
        symbol: t.symbol,
        side: t.side,
        closedAt: t.closed_at,
      }
    })
  }, [trades])

  if (data.length === 0) {
    return (
      <div className="flex h-44 items-center justify-center rounded-md border border-dashed border-border bg-card/20">
        <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--fg-3)]">
          aún no hay trades cerrados con R-multiple
        </p>
      </div>
    )
  }

  const finalR = data[data.length - 1]!.cumR
  const positive = finalR >= 0
  const stroke = positive ? "var(--long)" : "var(--short)"
  const gradientId = positive ? "rCurveGradPos" : "rCurveGradNeg"

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between">
        <span className="eyebrow">curva acumulada · R por trade</span>
        <span
          className="font-mono text-sm tabular-nums"
          style={{ color: stroke }}
        >
          {positive ? "+" : ""}
          {finalR.toFixed(2)}R total
        </span>
      </div>
      <div className="h-44 w-full">
        <ResponsiveContainer>
          <AreaChart
            data={data}
            margin={{ top: 8, right: 8, bottom: 4, left: 4 }}
          >
            <defs>
              <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={stroke} stopOpacity={0.3} />
                <stop offset="100%" stopColor={stroke} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid
              stroke="var(--color-border)"
              strokeOpacity={0.3}
              vertical={false}
            />
            <XAxis
              dataKey="n"
              stroke="var(--color-muted-foreground)"
              tick={{ fontSize: 10, fontFamily: "var(--font-mono)" }}
              tickFormatter={(n: number) => `#${n}`}
              minTickGap={20}
            />
            <YAxis
              dataKey="cumR"
              stroke="var(--color-muted-foreground)"
              tick={{ fontSize: 10, fontFamily: "var(--font-mono)" }}
              tickFormatter={(n: number) =>
                `${n >= 0 ? "+" : ""}${n.toFixed(1)}R`
              }
              width={48}
              domain={["dataMin", "dataMax"]}
            />
            <Tooltip
              contentStyle={{
                background: "var(--color-popover)",
                border: "1px solid var(--color-border)",
                borderRadius: "0.375rem",
                fontFamily: "var(--font-mono)",
                fontSize: "11px",
              }}
              cursor={{
                stroke: "var(--color-muted-foreground)",
                strokeOpacity: 0.5,
                strokeDasharray: "3 3",
              }}
              content={(p) => {
                if (!p.active || !p.payload || p.payload.length === 0)
                  return null
                const point = p.payload[0]?.payload as
                  | StrategyRCurvePoint
                  | undefined
                if (!point) return null
                const sideTone =
                  point.side === "long" ? "var(--long)" : "var(--short)"
                const rTone = point.r >= 0 ? "var(--long)" : "var(--short)"
                return (
                  <div
                    style={{
                      background: "var(--color-popover)",
                      border: "1px solid var(--color-border)",
                      borderRadius: "0.375rem",
                      fontFamily: "var(--font-mono)",
                      fontSize: "11px",
                      padding: "6px 8px",
                      lineHeight: 1.4,
                    }}
                  >
                    <div>
                      <span>#{point.n} · </span>
                      <span style={{ color: sideTone }}>
                        {point.symbol} {point.side}
                      </span>
                    </div>
                    <div>
                      este trade:{" "}
                      <span style={{ color: rTone }}>
                        {point.r >= 0 ? "+" : ""}
                        {point.r.toFixed(2)}R
                      </span>
                    </div>
                    <div style={{ color: "var(--fg-3)" }}>
                      acumulado{" "}
                      <span style={{ color: "var(--foreground)" }}>
                        {point.cumR >= 0 ? "+" : ""}
                        {point.cumR.toFixed(2)}R
                      </span>
                      {point.closedAt
                        ? ` · ${formatRelative(point.closedAt)}`
                        : ""}
                    </div>
                  </div>
                )
              }}
            />
            <ReferenceLine
              y={0}
              stroke="var(--color-muted-foreground)"
              strokeDasharray="3 3"
              strokeOpacity={0.5}
            />
            <Area
              type="monotone"
              dataKey="cumR"
              stroke={stroke}
              strokeWidth={1.5}
              fill={`url(#${gradientId})`}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

function formatRelative(iso: string): string {
  const dt = new Date(iso)
  const diffMs = Date.now() - dt.getTime()
  const min = Math.round(diffMs / 60_000)
  if (min < 1) return "ahora"
  if (min < 60) return `hace ${min}m`
  const h = Math.round(min / 60)
  if (h < 24) return `hace ${h}h`
  const d = Math.round(h / 24)
  return `hace ${d}d`
}
