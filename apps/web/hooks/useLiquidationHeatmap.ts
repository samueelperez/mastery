/**
 * Hook to fetch + cache the liquidation-heatmap history into the
 * `chart-overlays` store. Uses `@tanstack/react-query` so it inherits the
 * project's query devtools, suspense readiness, and the same patterns
 * used by `app/journal/page.tsx` and `app/research/`.
 *
 * Lifecycle:
 *   1. Component (CandleChart parent) mounts → reads `heatmapEnabled`
 *      from the store. If `false`, the hook short-circuits and the
 *      query stays disabled (no network).
 *   2. When enabled, the query fires on mount for the current
 *      `(symbol, timeframe, lookbackHours)` triple, refetches every 60s
 *      and on window focus.
 *   3. On each successful response, the snapshots + etag are pushed to
 *      the store via `setHeatmap(symbol, ...)`. The primitive's effect
 *      in CandleChart picks them up and repaints.
 *   4. On 304 (server says nothing changed), the fetcher resolves to
 *      `null` and the store is NOT touched — saves a re-render.
 */

"use client"

import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { useEffect } from "react"

import {
  type HeatmapHistoryDTO,
  fetchLiquidationHistory,
} from "@/lib/api/liquidation"
import { useChartOverlays } from "@/lib/store/chart-overlays"

interface UseLiquidationHeatmapArgs {
  symbol: string
  timeframe: string
}

export function useLiquidationHeatmap({
  symbol,
  timeframe,
}: UseLiquidationHeatmapArgs): {
  isLoading: boolean
  isError: boolean
  asOf: string | null
  zoneCount: number
} {
  const enabled = useChartOverlays((s) => s.heatmapEnabled)
  const lookbackHours = useChartOverlays((s) => s.heatmapLookback)
  const setHeatmap = useChartOverlays((s) => s.setHeatmap)
  const cachedEtag = useChartOverlays(
    (s) => s.bySymbol[symbol]?.heatmap?.etag ?? null,
  )

  const { data, isLoading, isError } = useQuery<HeatmapHistoryDTO | null>({
    queryKey: ["liquidation", "heatmap", symbol, timeframe, lookbackHours],
    queryFn: ({ signal }) =>
      fetchLiquidationHistory({
        symbol,
        timeframe,
        lookbackHours,
        signal,
        ifNoneMatch: cachedEtag ?? undefined,
      }),
    enabled,
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
    staleTime: 30_000,
    placeholderData: keepPreviousData,
  })

  // Push to the store on every successful response. Skip when the
  // fetcher returned null (304 no-change) — the store already has the
  // most recent data.
  useEffect(() => {
    if (!enabled) {
      setHeatmap(symbol, null)
      return
    }
    if (!data) return
    setHeatmap(symbol, {
      snapshots: data.snapshots,
      etag: data.etag,
      asOf: data.as_of,
      lookbackHours,
    })
  }, [data, enabled, lookbackHours, setHeatmap, symbol])

  return {
    isLoading,
    isError,
    asOf: data?.as_of ?? null,
    zoneCount: (data?.snapshots ?? []).reduce(
      (acc, s) => acc + s.zones.length,
      0,
    ),
  }
}
