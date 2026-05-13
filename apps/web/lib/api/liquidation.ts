/**
 * Client for `/liquidation/heatmap/{symbol}/{tf}/history`. Used by the
 * `useLiquidationHeatmap` hook (react-query) to populate the
 * `chart-overlays` store. The shapes match exactly what
 * `apps/api/app/liquidation/routes.py` returns.
 */

import { BEARER_TOKEN_KEY } from "@/lib/core/auth/auth-client"
import { env } from "@/lib/core/env"

// ---------- DTOs ----------

export interface HeatmapZoneDTO {
  price_low: number
  price_high: number
  side: "long_liq" | "short_liq"
  est_volume_usd: number
  source_breakdown: Record<string, number>
  confidence: "low" | "medium" | "high"
}

export interface HeatmapSnapshotDTO {
  /** ISO-8601 UTC. */
  ts: string
  zones: HeatmapZoneDTO[]
}

export interface HeatmapHistoryDTO {
  symbol: string
  timeframe: string
  lookback_hours: number
  min_volume_usd: number
  /** ISO-8601 UTC of the most recent snapshot, or null if empty. */
  as_of: string | null
  snapshots: HeatmapSnapshotDTO[]
  /** Weak ETag the FE can echo via `If-None-Match` to get a 304 when
   *  nothing changed. react-query handles this transparently when we
   *  thread it through `headers`. */
  etag: string
}

// ---------- Fetcher ----------

interface FetchHistoryOpts {
  symbol: string
  timeframe: string
  lookbackHours: number
  minVolumeUsd?: number
  signal?: AbortSignal
  /** Last seen ETag, sent as `If-None-Match`. When the server returns 304
   *  the fetcher resolves to `null` so the caller can skip a redraw. */
  ifNoneMatch?: string | null
}

function readBearerToken(): string | null {
  if (typeof window === "undefined") return null
  return window.localStorage.getItem(BEARER_TOKEN_KEY)
}

export async function fetchLiquidationHistory(
  opts: FetchHistoryOpts,
): Promise<HeatmapHistoryDTO | null> {
  const url = new URL(
    `${env.apiUrl}/liquidation/heatmap/${encodeURIComponent(
      opts.symbol,
    )}/${encodeURIComponent(opts.timeframe)}/history`,
  )
  url.searchParams.set("lookback_hours", String(opts.lookbackHours))
  if (opts.minVolumeUsd != null) {
    url.searchParams.set("min_volume_usd", String(opts.minVolumeUsd))
  }

  const headers = new Headers()
  const token = readBearerToken()
  if (token) headers.set("Authorization", `Bearer ${token}`)
  if (opts.ifNoneMatch) headers.set("If-None-Match", opts.ifNoneMatch)

  const res = await fetch(url, {
    credentials: "include",
    signal: opts.signal,
    headers,
  })

  if (res.status === 304) return null
  if (res.status === 503) {
    // Scheduler hasn't populated yet — caller treats this as "empty"
    // rather than an error.
    return {
      symbol: opts.symbol,
      timeframe: opts.timeframe,
      lookback_hours: opts.lookbackHours,
      min_volume_usd: opts.minVolumeUsd ?? 0,
      as_of: null,
      snapshots: [],
      etag: "empty",
    }
  }
  if (!res.ok) {
    throw new Error(
      `fetchLiquidationHistory failed: ${res.status} ${res.statusText}`,
    )
  }
  return (await res.json()) as HeatmapHistoryDTO
}
