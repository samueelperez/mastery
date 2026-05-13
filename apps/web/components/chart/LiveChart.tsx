"use client"

import type { IChartApi } from "lightweight-charts"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import { OverlayPanel } from "@/components/dashboard/OverlayPanel"
import { useLiquidationHeatmap } from "@/hooks/useLiquidationHeatmap"
import { useChartOverlays } from "@/lib/store/chart-overlays"

import type { HeatmapSnapshotDTO } from "@/lib/api/liquidation"

import { CandleChart } from "./CandleChart"
import { ChartLegend } from "./ChartLegend"
import { HeatmapColorScaleLegend } from "./overlays/HeatmapColorScaleLegend"
import { HeatmapTooltip } from "./overlays/HeatmapTooltip"
import type { LiquidationHeatmapPrimitive } from "./overlays/LiquidationHeatmapPrimitive"
import { useActiveSetupBridge } from "./useActiveSetupBridge"
import { useLiveCandles } from "./useLiveCandles"

function computeMedianFromBundle(
  snapshots: HeatmapSnapshotDTO[] | undefined,
): number | null {
  if (!snapshots || snapshots.length === 0) return null
  const vols: number[] = []
  for (const s of snapshots) {
    for (const z of s.zones) {
      if (Number.isFinite(z.est_volume_usd) && z.est_volume_usd > 0) {
        vols.push(z.est_volume_usd)
      }
    }
  }
  if (vols.length === 0) return null
  vols.sort((a, b) => a - b)
  const mid = Math.floor(vols.length / 2)
  return vols.length % 2 === 1 ? vols[mid]! : (vols[mid - 1]! + vols[mid]!) / 2
}

export interface LiveChartProps {
  symbol: string
  timeframe: string
  className?: string
}

export function LiveChart({ symbol, timeframe, className }: LiveChartProps) {
  const { initial, loading, error, live, wsConnected } = useLiveCandles(
    symbol,
    timeframe,
    500,
  )
  const overlays = useChartOverlays((s) => s.bySymbol[symbol] ?? null)
  const minimalMode = useChartOverlays((s) => s.minimalMode)
  const heatmapEnabled = useChartOverlays((s) => s.heatmapEnabled)
  // Hidrata `tradeIdeas[]` desde DB con todos los setups pending/active del
  // símbolo. Sin esto, recargar la página pierde las zonas aunque
  // `journal_trades` las siga teniendo abiertas.
  useActiveSetupBridge(symbol)
  // Heatmap del liquidation engine (HM-PR2). El backend solo guarda
  // snapshots por `1h|4h|1d` — para timeframes intra-hora del chart
  // (1m, 15m), caemos a `4h` que es el aggregation más útil para
  // contexto. Lookback (1/6/24/168h) lo controla el usuario desde
  // OverlayPanel; toggle on/off es persisted preference.
  const heatmapTf = (["1h", "4h", "1d"] as const).includes(
    timeframe as "1h" | "4h" | "1d",
  )
    ? timeframe
    : "4h"
  useLiquidationHeatmap({ symbol, timeframe: heatmapTf })

  // Selección activa del switcher — UI-only, no persiste cross-session.
  // Si el array de ideas cambia y la actual ya no existe (setup cerró),
  // resetea al primero (más reciente).
  const ideas = overlays?.tradeIdeas ?? []
  const [activeIdeaId, setActiveIdeaId] = useState<string | null>(null)
  useEffect(() => {
    if (ideas.length === 0) {
      if (activeIdeaId !== null) setActiveIdeaId(null)
      return
    }
    if (!activeIdeaId || !ideas.find((i) => i.id === activeIdeaId)) {
      setActiveIdeaId(ideas[0]!.id)
    }
  }, [ideas, activeIdeaId])

  const activeIdea = useMemo(
    () => ideas.find((i) => i.id === activeIdeaId) ?? ideas[0] ?? null,
    [ideas, activeIdeaId],
  )

  // Heatmap tooltip wiring — CandleChart hands us the chart + primitive
  // refs through `onHeatmapReady` so the tooltip overlay (a sibling
  // React component) can subscribe to crosshair-move on the same chart
  // instance the primitive is drawing on.
  const [heatmapChart, setHeatmapChart] = useState<IChartApi | null>(null)
  const [heatmapPrimitive, setHeatmapPrimitive] =
    useState<LiquidationHeatmapPrimitive | null>(null)
  const handleHeatmapReady = useCallback(
    (chart: IChartApi | null, primitive: LiquidationHeatmapPrimitive | null) => {
      setHeatmapChart(chart)
      setHeatmapPrimitive(primitive)
    },
    [],
  )
  // Container rect for the tooltip's edge-flipping logic.
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [containerRect, setContainerRect] = useState<DOMRect | null>(null)
  useEffect(() => {
    if (!containerRef.current) return
    const el = containerRef.current
    const update = () => setContainerRect(el.getBoundingClientRect())
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  return (
    <div className="flex h-full w-full min-h-0 flex-col gap-2">
      <div className="flex shrink-0 items-center justify-between text-xs text-[var(--fg-2)]">
        <span className="font-mono uppercase tracking-[0.08em]">
          {symbol} · {timeframe}
          {live && (
            <span className="ml-3 font-mono normal-case tracking-normal tabular-nums text-foreground">
              {live.c.toLocaleString(undefined, { minimumFractionDigits: 2 })}
            </span>
          )}
        </span>
        <span className="flex items-center gap-2">
          <OverlayPanel symbol={symbol} />
          {error ? (
            <span className="text-destructive">load error</span>
          ) : loading ? (
            <span>loading…</span>
          ) : null}
          <span
            data-status={wsConnected ? "live" : "disc"}
            className="inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.14em]"
          >
            <span
              aria-hidden
              className={`dot ${wsConnected ? "dot-live" : "bg-[var(--fg-4)]"}`}
            />
            {wsConnected ? "live" : "offline"}
          </span>
        </span>
      </div>
      <div ref={containerRef} className="relative min-h-0 flex-1 w-full">
        <CandleChart
          initial={initial}
          live={live}
          overlays={overlays}
          activeIdea={activeIdea}
          activeTimeframe={timeframe}
          minimalMode={minimalMode}
          onHeatmapReady={handleHeatmapReady}
          className={`h-full w-full ${className ?? ""}`}
        />
        <ChartLegend
          overlays={overlays}
          initial={initial}
          activeTimeframe={timeframe}
          minimalMode={minimalMode}
          ideas={ideas}
          activeIdeaId={activeIdea?.id ?? null}
          onSelectIdea={setActiveIdeaId}
        />
        <HeatmapTooltip
          chart={heatmapChart}
          primitive={heatmapPrimitive}
          containerRect={containerRect}
        />
        <HeatmapColorScaleLegend
          visible={heatmapEnabled && (overlays?.heatmap?.snapshots.length ?? 0) > 0}
          medianVolumeUsd={computeMedianFromBundle(overlays?.heatmap?.snapshots)}
        />
      </div>
    </div>
  )
}
