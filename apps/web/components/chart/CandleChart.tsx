"use client"

import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type Time,
} from "lightweight-charts"
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

    // Pull live token values from the document so the chart matches whatever
    // globals.css (.dark) defines. Browsers normalize `oklch(…)` to `lab(…)`
    // in getComputedStyle and `ctx.fillStyle` preserves the color space —
    // Lightweight Charts can't parse either. The reliable fix is to actually
    // RASTERIZE a 1×1 pixel and read the RGBA bytes back; that always returns
    // honest sRGB integers regardless of the source color space.
    const probeCanvas = document.createElement("canvas")
    probeCanvas.width = 1
    probeCanvas.height = 1
    const probe = probeCanvas.getContext("2d", { willReadFrequently: true })
    const toRgb = (cssValue: string, fallback: string): string => {
      if (!probe) return fallback
      probe.clearRect(0, 0, 1, 1)
      try {
        probe.fillStyle = cssValue
      } catch {
        return fallback
      }
      probe.fillRect(0, 0, 1, 1)
      const [r, g, b, a] = probe.getImageData(0, 0, 1, 1).data
      if (a === 255) {
        return `#${[r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("")}`
      }
      return `rgba(${r}, ${g}, ${b}, ${(a / 255).toFixed(3)})`
    }
    const cs = getComputedStyle(document.documentElement)
    const token = (name: string, fallback: string): string =>
      toRgb(cs.getPropertyValue(name).trim() || fallback, fallback)
    const fg = token("--color-foreground", "#f8fafc")
    const border = token("--color-border", "#334155")
    const success = token("--color-success", "#10b981")
    const destructive = token("--color-destructive", "#ef4444")

    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: fg,
        fontFamily: "var(--font-sans)",
      },
      grid: {
        vertLines: { color: border, style: 1 },
        horzLines: { color: border, style: 1 },
      },
      rightPriceScale: { borderColor: border },
      timeScale: {
        borderColor: border,
        timeVisible: true,
        secondsVisible: false,
      },
      // Free crosshair — Normal mode lets the line follow the cursor anywhere
      // on the canvas (Magnet mode would snap horizontally to the nearest bar).
      crosshair: { mode: CrosshairMode.Normal },
    })

    const series = chart.addSeries(CandlestickSeries, {
      upColor: success,
      downColor: destructive,
      wickUpColor: success,
      wickDownColor: destructive,
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
