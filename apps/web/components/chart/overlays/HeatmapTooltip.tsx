/**
 * Hover tooltip for the liquidation heatmap. Subscribes to the chart's
 * crosshair-move event, asks the primitive for the zone under the
 * cursor, and renders a small floating card with the zone's details.
 *
 * Why a React overlay (not a canvas-drawn tooltip): React lets us
 * compose the same UI tokens (typography, spacing, tabular nums) the
 * rest of the dashboard uses, and it cleans up gracefully when the
 * heatmap is toggled off. The tooltip lives in the relative container
 * that wraps `<CandleChart>` and `<ChartLegend>` in `LiveChart.tsx`.
 */

"use client"

import type { IChartApi } from "lightweight-charts"
import { useEffect, useState } from "react"

import { cn } from "@/lib/core/utils"

import type {
  HeatmapSnapshot,
  HeatmapZone,
  LiquidationHeatmapPrimitive,
} from "./LiquidationHeatmapPrimitive"

interface HeatmapTooltipProps {
  /** Chart instance — `null` while the chart is mounting / unmounted. */
  chart: IChartApi | null
  /** Primitive instance — `null` when the heatmap is disabled. */
  primitive: LiquidationHeatmapPrimitive | null
  /** Width / height of the chart container, used to flip the tooltip
   *  when the cursor is near the right/bottom edge. */
  containerRect: DOMRect | null
}

interface Hit {
  snapshot: HeatmapSnapshot
  zone: HeatmapZone
  x: number
  y: number
}

export function HeatmapTooltip({
  chart,
  primitive,
  containerRect,
}: HeatmapTooltipProps) {
  const [hit, setHit] = useState<Hit | null>(null)

  useEffect(() => {
    if (!chart || !primitive) {
      setHit(null)
      return
    }
    const handler = (param: {
      point?: { x: number; y: number }
    }) => {
      if (!param.point) {
        setHit(null)
        return
      }
      const found = primitive.hitTestAt(param.point.x, param.point.y)
      if (!found) {
        setHit(null)
        return
      }
      setHit({ ...found, x: param.point.x, y: param.point.y })
    }
    chart.subscribeCrosshairMove(handler)
    return () => {
      chart.unsubscribeCrosshairMove(handler)
    }
  }, [chart, primitive])

  if (!hit || !containerRect) return null

  // Anchor the tooltip near the cursor, flipping when near the right
  // or bottom edge to avoid clipping.
  const TOOLTIP_W = 240
  const TOOLTIP_H = 110
  const margin = 12
  const flipX = hit.x + margin + TOOLTIP_W > containerRect.width
  const flipY = hit.y + margin + TOOLTIP_H > containerRect.height
  const left = flipX ? hit.x - margin - TOOLTIP_W : hit.x + margin
  const top = flipY ? hit.y - margin - TOOLTIP_H : hit.y + margin

  const ageMin = Math.max(
    0,
    Math.floor((Date.now() - new Date(hit.snapshot.ts).getTime()) / 60_000),
  )
  const sources = Object.entries(hit.zone.source_breakdown ?? {})
    .filter(([, v]) => v && v > 0)
    .map(([k]) => k.replace(/^[A-Z]_/, ""))
    .join(" + ")

  return (
    <div
      className={cn(
        "pointer-events-none absolute z-20",
        "rounded-md border border-border bg-card/95 backdrop-blur-sm",
        "px-2.5 py-2 font-mono text-[10px] leading-tight",
        "shadow-lg",
      )}
      style={{ left, top, width: TOOLTIP_W }}
      role="status"
      aria-live="polite"
    >
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[var(--fg-2)]">
          {fmtPrice(hit.zone.price_low)} – {fmtPrice(hit.zone.price_high)}
        </span>
        <span
          className={cn(
            "rounded px-1 text-[9px] uppercase tracking-[0.12em]",
            hit.zone.side === "long_liq"
              ? "bg-[color:var(--liq-long-soft,rgba(40,140,200,0.15))] text-[var(--fg-1)]"
              : "bg-[color:var(--liq-short-soft,rgba(220,80,40,0.15))] text-[var(--fg-1)]",
          )}
        >
          {hit.zone.side === "long_liq" ? "long liq" : "short liq"}
        </span>
      </div>
      <div className="mt-1 flex items-baseline justify-between">
        <span className="text-[var(--fg-3)]">est.</span>
        <span className="tabular text-foreground">
          {fmtUsd(hit.zone.est_volume_usd)}
        </span>
      </div>
      {sources && (
        <div className="flex items-baseline justify-between">
          <span className="text-[var(--fg-3)]">sources</span>
          <span className="text-[var(--fg-2)]">{sources}</span>
        </div>
      )}
      <div className="flex items-baseline justify-between">
        <span className="text-[var(--fg-3)]">snapshot</span>
        <span className="tabular text-[var(--fg-2)]">
          {ageMin === 0 ? "live" : `${ageMin}m ago`}
        </span>
      </div>
      {hit.zone.confidence && (
        <div className="flex items-baseline justify-between">
          <span className="text-[var(--fg-3)]">confidence</span>
          <span className="text-[var(--fg-2)]">{hit.zone.confidence}</span>
        </div>
      )}
    </div>
  )
}

function fmtPrice(p: number): string {
  if (p >= 1000) return `$${p.toLocaleString(undefined, { maximumFractionDigits: 1 })}`
  if (p >= 1) return `$${p.toFixed(3)}`
  return `$${p.toFixed(6)}`
}

function fmtUsd(v: number): string {
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`
  if (v >= 1e6) return `$${(v / 1e6).toFixed(2)}M`
  if (v >= 1e3) return `$${(v / 1e3).toFixed(0)}K`
  return `$${v.toFixed(0)}`
}
