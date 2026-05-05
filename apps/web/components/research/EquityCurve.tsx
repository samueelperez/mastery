"use client"

import { useMemo } from "react"
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

interface EquityCurveProps {
  curve: [string, number][]
  initialEquity: number
}

/** Backtests largos pueden tener 10k+ puntos (1 por bar de 1m sobre 1 mes).
 *  Recharts re-renderiza con cada hover y se ralentiza ~ O(n). Downsampleamos
 *  manteniendo siempre los extremos (first, last) y picos/valles (max/min),
 *  más una muestra uniforme entre ellos para preservar la silueta visual.
 *  Cap a ~2000 puntos — más allá, ojo humano no distingue. */
const TARGET_POINTS = 2000

function downsample(curve: [string, number][]): [string, number][] {
  if (curve.length <= TARGET_POINTS) return curve
  const stride = Math.ceil(curve.length / TARGET_POINTS)
  const out: [string, number][] = []
  // Anclamos primero y último explícitamente; el muestreo intermedio puede
  // saltarlos si stride no divide exacto.
  out.push(curve[0]!)
  for (let i = stride; i < curve.length - 1; i += stride) {
    out.push(curve[i]!)
  }
  out.push(curve[curve.length - 1]!)
  return out
}

/** Recorre la curve calculando peak running y devuelve el ts del peor DD
 *  (drawdown más negativo) junto con el equity en ese punto. Usado para
 *  anotar la curva con un dot en "aquí cayó al peor punto". */
function findWorstDd(
  curve: [string, number][],
): { ts: string; equity: number; ddPct: number } | null {
  if (curve.length === 0) return null
  let peak = curve[0]![1]
  let worst = { ts: curve[0]![0], equity: curve[0]![1], ddPct: 0 }
  for (const [ts, eq] of curve) {
    if (eq > peak) peak = eq
    const dd = peak > 0 ? ((eq - peak) / peak) * 100 : 0
    if (dd < worst.ddPct) worst = { ts, equity: eq, ddPct: dd }
  }
  return worst.ddPct < 0 ? worst : null
}

export function EquityCurve({ curve, initialEquity }: EquityCurveProps) {
  const data = useMemo(
    () =>
      downsample(curve).map(([ts, eq]) => ({
        ts,
        equity: eq,
        pnl_pct: (eq / initialEquity - 1) * 100,
      })),
    [curve, initialEquity],
  )
  const final = data.at(-1)?.equity ?? initialEquity
  const ret = (final / initialEquity - 1) * 100
  const worst = useMemo(() => findWorstDd(curve), [curve])

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-xs uppercase tracking-widest text-muted-foreground">
          curva de equity
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
              formatter={(value) => [`$${Number(value).toFixed(2)}`, "equity"]} /* technical name kept */
            />
            <ReferenceLine
              y={initialEquity}
              stroke="var(--color-muted-foreground)"
              strokeDasharray="3 3"
              strokeOpacity={0.5}
              label={{
                value: "inicial",
                position: "right",
                fill: "var(--color-muted-foreground)",
                fontSize: 10,
                fontFamily: "var(--font-mono)",
              }}
            />
            {worst && (
              <ReferenceDot
                x={worst.ts}
                y={worst.equity}
                r={4}
                fill="var(--color-destructive)"
                stroke="var(--color-destructive)"
                strokeWidth={1}
                label={{
                  value: `${worst.ddPct.toFixed(1)}% aquí`,
                  position: "top",
                  fill: "var(--color-destructive)",
                  fontSize: 10,
                  fontFamily: "var(--font-mono)",
                }}
              />
            )}
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
