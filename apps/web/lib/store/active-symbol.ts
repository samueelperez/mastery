"use client"

import { create } from "zustand"
import { persist } from "zustand/middleware"

/** Watchlist visible en la sidebar del dashboard. Debe coincidir con el
 * `WATCH_SYMBOLS` env del backend — si no coincide, símbolos del cliente
 * que no estén en backend devuelven OHLCV vacío.
 */
export const WATCH_SYMBOLS = [
  "BTCUSDT",
  "ETHUSDT",
  "SOLUSDT",
  "BNBUSDT",
] as const

export type WatchSymbol = (typeof WATCH_SYMBOLS)[number]

export const TIMEFRAMES = ["1m", "15m", "1h", "4h", "1d"] as const
export type Timeframe = (typeof TIMEFRAMES)[number]

export function isWatchSymbol(s: string): s is WatchSymbol {
  return (WATCH_SYMBOLS as readonly string[]).includes(s)
}
export function isTimeframe(s: string): s is Timeframe {
  return (TIMEFRAMES as readonly string[]).includes(s)
}

interface ActiveSymbolState {
  symbol: WatchSymbol
  timeframe: Timeframe
  setSymbol: (s: WatchSymbol) => void
  setTimeframe: (tf: Timeframe) => void
  setBoth: (s: WatchSymbol, tf?: Timeframe) => void
}

/** Store global del símbolo + timeframe activos. Persiste el último selecto
 * en localStorage para que sobreviva refresh. La sidebar y el chart leen aquí;
 * el chat lo escribe cuando el agente dispara `get_ohlcv` con un símbolo de
 * la watchlist (puente F-multi.D).
 */
export const useActiveSymbol = create<ActiveSymbolState>()(
  persist(
    (set) => ({
      symbol: "BTCUSDT",
      timeframe: "1h",
      setSymbol: (symbol) => set({ symbol }),
      setTimeframe: (timeframe) => set({ timeframe }),
      setBoth: (symbol, timeframe) =>
        set((state) => ({ symbol, timeframe: timeframe ?? state.timeframe })),
    }),
    {
      name: "trading-copilot:active-symbol",
      version: 1,
      // Sólo persistimos los campos serializables, no las setters.
      partialize: (state) => ({
        symbol: state.symbol,
        timeframe: state.timeframe,
      }),
    },
  ),
)
