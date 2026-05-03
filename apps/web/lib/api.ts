import { env } from "@/lib/env"

export interface CandleDTO {
  ts: string
  o: number
  h: number
  l: number
  c: number
  v: number
}

export interface OHLCVResponseDTO {
  exchange: string
  symbol: string
  timeframe: string
  count: number
  candles: CandleDTO[]
}

export async function fetchOhlcv(
  symbol: string,
  timeframe: string,
  opts: { limit?: number; signal?: AbortSignal } = {},
): Promise<OHLCVResponseDTO> {
  const limit = opts.limit ?? 1000
  const url = `${env.apiUrl}/ohlcv/${encodeURIComponent(symbol)}/${encodeURIComponent(timeframe)}?limit=${limit}`
  const res = await fetch(url, { signal: opts.signal })
  if (!res.ok) {
    throw new Error(`fetchOhlcv failed: ${res.status} ${res.statusText}`)
  }
  return (await res.json()) as OHLCVResponseDTO
}
