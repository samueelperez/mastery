import type { Time } from "lightweight-charts"

import type { CandleDTO } from "@/lib/core/api"

export interface LinePoint {
  time: Time
  value: number
}

function tsToTime(ts: string): Time {
  return Math.floor(new Date(ts).getTime() / 1000) as unknown as Time
}

/** EMA de N periodos sobre el cierre. Sigue la convención de "primer valor
 *  posible es la SMA del periodo, luego EMA exponencial".
 *
 *  k = 2 / (period + 1)
 *  EMA[i] = close[i] * k + EMA[i-1] * (1-k)
 *
 *  Si `candles.length < period`, devuelve []. */
export function computeEma(candles: CandleDTO[], period: number): LinePoint[] {
  if (period < 2 || candles.length < period) return []
  const out: LinePoint[] = []
  const k = 2 / (period + 1)

  // Primer EMA = SMA del periodo inicial.
  let sum = 0
  for (let i = 0; i < period; i++) sum += candles[i]!.c
  let ema = sum / period
  out.push({ time: tsToTime(candles[period - 1]!.ts), value: ema })

  for (let i = period; i < candles.length; i++) {
    ema = candles[i]!.c * k + ema * (1 - k)
    out.push({ time: tsToTime(candles[i]!.ts), value: ema })
  }
  return out
}

/** SMA de N periodos sobre el cierre — media móvil simple, ventana fija. */
export function computeSma(candles: CandleDTO[], period: number): LinePoint[] {
  if (period < 2 || candles.length < period) return []
  const out: LinePoint[] = []
  let sum = 0
  for (let i = 0; i < period; i++) sum += candles[i]!.c
  out.push({ time: tsToTime(candles[period - 1]!.ts), value: sum / period })
  for (let i = period; i < candles.length; i++) {
    sum += candles[i]!.c - candles[i - period]!.c
    out.push({ time: tsToTime(candles[i]!.ts), value: sum / period })
  }
  return out
}

export interface BollingerBands {
  mid: LinePoint[]
  upper: LinePoint[]
  lower: LinePoint[]
}

/** Bollinger Bands clásicas: SMA(period) ± stdDev * multiplier (típico
 *  20/2). La stdDev se computa con la población (no muestra) sobre la
 *  ventana, igual que pandas `.rolling(period).std(ddof=0)`. */
export function computeBollinger(
  candles: CandleDTO[],
  period = 20,
  multiplier = 2,
): BollingerBands {
  if (period < 2 || candles.length < period) {
    return { mid: [], upper: [], lower: [] }
  }
  const mid: LinePoint[] = []
  const upper: LinePoint[] = []
  const lower: LinePoint[] = []

  for (let i = period - 1; i < candles.length; i++) {
    let sum = 0
    for (let j = i - period + 1; j <= i; j++) sum += candles[j]!.c
    const mean = sum / period

    let sqSum = 0
    for (let j = i - period + 1; j <= i; j++) {
      const diff = candles[j]!.c - mean
      sqSum += diff * diff
    }
    const std = Math.sqrt(sqSum / period)
    const time = tsToTime(candles[i]!.ts)
    mid.push({ time, value: mean })
    upper.push({ time, value: mean + multiplier * std })
    lower.push({ time, value: mean - multiplier * std })
  }
  return { mid, upper, lower }
}

/** VWAP cumulativo desde el primer candle (sesión = todo el rango cargado).
 *  No reseteamos por sesión diaria — el chart muestra una ventana móvil y
 *  reseteo añadiría complejidad sin beneficio claro para este nivel. */
export function computeVwap(candles: CandleDTO[]): LinePoint[] {
  if (candles.length === 0) return []
  const out: LinePoint[] = []
  let cumPv = 0
  let cumV = 0
  for (const c of candles) {
    const typical = (c.h + c.l + c.c) / 3
    cumPv += typical * c.v
    cumV += c.v
    out.push({
      time: tsToTime(c.ts),
      value: cumV > 0 ? cumPv / cumV : typical,
    })
  }
  return out
}
