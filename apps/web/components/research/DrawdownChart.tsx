"use client"

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

interface DrawdownChartProps {
  curve: [string, number][]
}

export function DrawdownChart({ curve }: DrawdownChartProps) {
  // Compute running peak then drawdown_pct = (eq - peak)/peak (negative).
  const data = curve.reduce<{ peak: number; rows: { ts: string; dd_pct: number }[] }>(
    (acc, [ts, eq]) => {
      const peak = Math.max(acc.peak, eq)
      acc.rows.push({ ts, dd_pct: ((eq - peak) / peak) * 100 })
      return { peak, rows: acc.rows }
    },
    { peak: curve[0]?.[1] ?? 1, rows: [] },
  ).rows
  const worst = data.reduce((acc, d) => Math.min(acc, d.dd_pct), 0)
  const worstTs = data.find((d) => d.dd_pct === worst)?.ts

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-xs uppercase tracking-widest text-muted-foreground">
          drawdown
        </span>
        <span className="font-mono text-sm tabular-nums text-destructive">
          {worst.toFixed(2)}%
        </span>
      </div>
      <div className="h-40 w-full">
        <ResponsiveContainer>
          <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 8, left: 8 }}>
            <defs>
              <linearGradient id="ddFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--color-destructive)" stopOpacity={0} />
                <stop offset="100%" stopColor="var(--color-destructive)" stopOpacity={0.4} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="var(--color-border)" strokeOpacity={0.3} vertical={false} />
            <XAxis
              dataKey="ts"
              tickFormatter={fmtDate}
              stroke="var(--color-muted-foreground)"
              tick={{ fontSize: 10, fontFamily: "var(--font-mono)" }}
              minTickGap={50}
            />
            <YAxis
              dataKey="dd_pct"
              stroke="var(--color-muted-foreground)"
              tick={{ fontSize: 10, fontFamily: "var(--font-mono)" }}
              tickFormatter={(n) => `${n.toFixed(0)}%`}
              width={50}
              domain={[(min: number) => Math.floor(min * 1.1), 0]}
            />
            <Tooltip
              contentStyle={{
                background: "var(--color-popover)",
                border: "1px solid var(--color-border)",
                borderRadius: "0.375rem",
                fontFamily: "var(--font-mono)",
                fontSize: "11px",
              }}
              labelFormatter={(label) => fmtDate(label as string | number)}
              formatter={(value) => [`${Number(value).toFixed(2)}%`, "drawdown"]}
            />
            {worstTs && (
              <ReferenceLine
                x={worstTs}
                stroke="var(--color-destructive)"
                strokeDasharray="2 2"
                strokeOpacity={0.6}
                label={{
                  value: `${worst.toFixed(1)}%`,
                  position: "top",
                  fill: "var(--color-destructive)",
                  fontSize: 10,
                  fontFamily: "var(--font-mono)",
                }}
              />
            )}
            <Area
              type="monotone"
              dataKey="dd_pct"
              stroke="var(--color-destructive)"
              strokeWidth={1}
              fill="url(#ddFill)"
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

function fmtDate(ts: string | number): string {
  const d = new Date(ts)
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
}
