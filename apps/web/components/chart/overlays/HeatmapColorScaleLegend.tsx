/**
 * Compact gradient legend for the liquidation heatmap. Anchored bottom-
 * right of the chart container; only rendered when the heatmap toggle
 * is on AND there are zones to show. Helps the operator translate
 * "this band is yellow" → "this band is ~$10M of leverage".
 *
 * Numbers on the rampa are anchored at the same log-stops the primitive
 * uses internally (median × {0.1, 1, 10, 100}) so the legend is honest
 * about how the colour ↔ volume mapping works.
 */

"use client"

import { cn } from "@/lib/core/utils"

import { HEATMAP_GRADIENT_STOPS } from "./heatmapColorScale"

interface HeatmapColorScaleLegendProps {
  visible: boolean
  /** Median volume across the current snapshot batch — used to compute
   *  the dollar anchors. Pass `null` while data is loading. */
  medianVolumeUsd: number | null
}

export function HeatmapColorScaleLegend({
  visible,
  medianVolumeUsd,
}: HeatmapColorScaleLegendProps) {
  if (!visible) return null

  const gradientCss = `linear-gradient(to right, ${HEATMAP_GRADIENT_STOPS.join(", ")})`
  const median = medianVolumeUsd && medianVolumeUsd > 0 ? medianVolumeUsd : null

  return (
    <div
      className={cn(
        "pointer-events-none absolute bottom-3 right-3 z-10",
        "rounded-md border border-border bg-card/85 backdrop-blur-sm",
        "px-2 py-1.5 font-mono text-[9px]",
      )}
      role="img"
      aria-label="liquidation heatmap intensity scale"
    >
      <div className="mb-1 flex items-center justify-between gap-3">
        <span className="text-[var(--fg-3)] uppercase tracking-[0.12em]">
          liq intensity
        </span>
        {median && (
          <span className="tabular text-[var(--fg-3)]">med ≈ {fmtUsd(median)}</span>
        )}
      </div>
      <div
        className="h-1.5 w-36 rounded-sm"
        style={{ background: gradientCss }}
        aria-hidden
      />
      {median ? (
        <div className="mt-1 flex justify-between text-[var(--fg-3)] tabular">
          <span>{fmtUsd(median * 0.1)}</span>
          <span>{fmtUsd(median)}</span>
          <span>{fmtUsd(median * 10)}</span>
          <span>{fmtUsd(median * 100)}</span>
        </div>
      ) : (
        <div className="mt-1 text-[var(--fg-3)]">low → high</div>
      )}
    </div>
  )
}

function fmtUsd(v: number): string {
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`
  if (v >= 1e3) return `${(v / 1e3).toFixed(0)}K`
  return `${v.toFixed(0)}`
}
