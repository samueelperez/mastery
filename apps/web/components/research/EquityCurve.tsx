"use client"

import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

interface EquityCurveProps {
  curve: [string, number][]
  initialEquity: number
}

export function EquityCurve({ curve, initialEquity }: EquityCurveProps) {
  const data = curve.map(([ts, eq]) => ({
    ts,
    equity: eq,
    pnl_pct: (eq / initialEquity - 1) * 100,
  }))
  const final = data.at(-1)?.equity ?? initialEquity
  const ret = (final / initialEquity - 1) * 100

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-xs uppercase tracking-widest text-muted-foreground">
          equity curve
        </span>
        <span
          className={`font-mono text-sm tabular-nums ${ret >= 0 ? "text-primary" : "text-destructive"}`}
        >
          {ret >= 0 ? "+" : ""}
          {ret.toFixed(2)}%
        </span>
      </div>
      <div className="h-64 w-full">
        <ResponsiveContainer>
          <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 8, left: 8 }}>
            <defs>
              <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--color-primary)" stopOpacity={0.3} />
                <stop offset="100%" stopColor="var(--color-primary)" stopOpacity={0} />
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
              dataKey="equity"
              stroke="var(--color-muted-foreground)"
              tick={{ fontSize: 10, fontFamily: "var(--font-mono)" }}
              tickFormatter={(n) => `$${(n / 1000).toFixed(1)}k`}
              width={60}
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
              labelFormatter={(label) => fmtDate(label as string | number)}
              formatter={(value) => [`$${Number(value).toFixed(2)}`, "equity"]}
            />
            <Area
              type="monotone"
              dataKey="equity"
              stroke="var(--color-primary)"
              strokeWidth={1.5}
              fill="url(#equityFill)"
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
