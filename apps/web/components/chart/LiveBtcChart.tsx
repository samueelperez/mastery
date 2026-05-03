"use client"

import { useLiveCandles } from "./useLiveCandles"
import { CandleChart } from "./CandleChart"

export interface LiveBtcChartProps {
  symbol: string
  timeframe: string
  className?: string
}

export function LiveBtcChart({ symbol, timeframe, className }: LiveBtcChartProps) {
  const { initial, loading, error, live, wsConnected } = useLiveCandles(symbol, timeframe, 500)

  return (
    <div className="flex h-full w-full flex-col gap-2">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>
          {symbol} · {timeframe}
          {live && (
            <span className="ml-3 font-mono text-foreground">
              {live.c.toLocaleString(undefined, { minimumFractionDigits: 2 })}
            </span>
          )}
        </span>
        <span className="flex items-center gap-2">
          {error ? (
            <span className="text-destructive">load error</span>
          ) : loading ? (
            <span>loading…</span>
          ) : null}
          <span
            data-status={wsConnected ? "live" : "disc"}
            className="inline-flex items-center gap-1.5"
          >
            <span
              aria-hidden
              className={`size-2 rounded-full ${wsConnected ? "bg-emerald-500" : "bg-muted-foreground/40"}`}
            />
            {wsConnected ? "live" : "offline"}
          </span>
        </span>
      </div>
      <CandleChart initial={initial} live={live} className={className} />
    </div>
  )
}
