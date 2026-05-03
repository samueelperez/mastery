"use client"

import { CandlestickSeries, ColorType, createChart, type IChartApi, type ISeriesApi, type Time } from "lightweight-charts"
import { useEffect, useRef } from "react"

import type { CandleDTO } from "@/lib/api"
import type { LiveCandle } from "./useLiveCandles"

/**
 * Lightweight Charts wrapper.
 *
 * Performance rules from the plan:
 * - Chart and series API handles are kept in `useRef`, NOT `useState`. Each WS tick calls
 *   `series.update(candle)` directly without re-rendering React.
 * - Initial data goes in once via `series.setData()`, then ALL further mutations are deltas.
 * - Throttling is unnecessary at the React layer — Lightweight Charts repaints at most once
 *   per animation frame regardless of update rate.
 * - Resize handled with ResizeObserver (no debounce needed for layout-driven resizes).
 */
export interface CandleChartProps {
  initial: CandleDTO[] | undefined
  live: LiveCandle | null
  className?: string
}

function toLwcCandle(c: CandleDTO) {
  return {
    time: (Math.floor(new Date(c.ts).getTime() / 1000) as unknown) as Time,
    open: c.o,
    high: c.h,
    low: c.l,
    close: c.c,
  }
}

export function CandleChart({ initial, live, className }: CandleChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null)
  const seededRef = useRef(false)

  // Mount + unmount: create / destroy the chart instance.
  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "rgba(248, 250, 252, 0.85)", // matches MASTER --color-foreground
        fontFamily: "var(--font-sans)",
      },
      grid: {
        vertLines: { color: "rgba(51, 65, 85, 0.2)" }, // MASTER --color-border at low alpha
        horzLines: { color: "rgba(51, 65, 85, 0.2)" },
      },
      rightPriceScale: { borderColor: "rgba(51, 65, 85, 0.5)" },
      timeScale: {
        borderColor: "rgba(51, 65, 85, 0.5)",
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: { mode: 1 },
    })

    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#16a34a", // green-600
      downColor: "#ef4444", // MASTER --color-destructive
      wickUpColor: "#16a34a",
      wickDownColor: "#ef4444",
      borderVisible: false,
    })

    chartRef.current = chart
    seriesRef.current = series
    seededRef.current = false

    return () => {
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
      seededRef.current = false
    }
  }, [])

  // Seed with initial historical data exactly once.
  useEffect(() => {
    if (!seriesRef.current || !initial || seededRef.current) return
    if (initial.length === 0) return
    seriesRef.current.setData(initial.map(toLwcCandle))
    chartRef.current?.timeScale().fitContent()
    seededRef.current = true
  }, [initial])

  // Apply each WS tick as a delta. No React re-render.
  useEffect(() => {
    if (!seriesRef.current || !live || !seededRef.current) return
    seriesRef.current.update(toLwcCandle(live))
  }, [live])

  return <div ref={containerRef} className={className} />
}
