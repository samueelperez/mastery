"use client"

import { create } from "zustand"
import { persist } from "zustand/middleware"

import type { Timeframe } from "@/lib/chat/types"

/** Indicadores activos (derivados de get_indicators / get_multi_tf_confluence).
 *  Los VALORES no se guardan — el frontend los recomputa desde los candles
 *  para el timeframe actual (ver overlays/computeEma.ts). Sólo guardamos
 *  qué periodos pidió el agente. */
export interface IndicatorOverlays {
  ema: number[]    // [21, 55, 200]
  sma: number[]    // []
  bbands: boolean  // 20-period BB, ±2σ
  vwap: boolean    // sesión
}

export interface PivotPoint {
  ts: string
  price: number
}

export interface SrLevel {
  price: number
  touches: number
}

export interface StructureOverlays {
  /** Timeframe en el que el agente computó el structure. Los pivots SOLO
   *  se renderizan cuando coincide con el tf activo (los timestamps no
   *  alinean cross-tf). S/R en cambio son precios y son cross-tf. */
  tf: Timeframe
  asOfTs: string
  swingHighs: PivotPoint[]
  swingLows: PivotPoint[]
  support: SrLevel[]
  resistance: SrLevel[]
  trendLabel: "HH_HL" | "LH_LL" | "mixed" | "indeterminate"
}

export interface TradeIdeaTarget {
  label: string
  price: number
}

export interface TradeIdeaOverlay {
  /** ID estable para discriminar entre múltiples setups del mismo símbolo
   *  en el switcher del chart. Para setups hidratados desde DB es el
   *  `setup_id` (uuid de `journal_trades`). Para los que vienen del chat
   *  antes de persistir, usamos `chat-${proposedAtSec}` como placeholder
   *  hasta que el siguiente refetch del setup-bridge lo reemplace. */
  id: string
  direction: "long" | "short"
  entry: number
  stopLoss: number
  targets: TradeIdeaTarget[]
  tf: Timeframe
  /** Unix timestamp en segundos del momento en que el setup fue propuesto.
   *  Se usa como anchor para detectar entry-hit / SL-hit / TP-hit en velas
   *  POSTERIORES — sin esto, el detector confundiría velas históricas con
   *  hits reales. Lo captura `addTradeIdea` automáticamente con
   *  `Date.now()` si no viene explícito. */
  proposedAtSec?: number
}

export interface OverlayBundle {
  indicators: IndicatorOverlays
  structure: StructureOverlays | null
  /** Lista de setups pending/active del símbolo. El chart usa un switcher
   *  para renderizar uno a la vez (zona + price lines), pero todos viven
   *  en este array para que el `ChartLegend` muestre `1/N` y permita
   *  alternar. Closed quedan fuera (solo en `/journal`). */
  tradeIdeas: TradeIdeaOverlay[]
}

export const EMPTY_INDICATORS: IndicatorOverlays = {
  ema: [],
  sma: [],
  bbands: false,
  vwap: false,
}

export const EMPTY_BUNDLE: OverlayBundle = {
  indicators: EMPTY_INDICATORS,
  structure: null,
  tradeIdeas: [],
}

interface ChartOverlaysState {
  /** symbol → OverlayBundle. Si no hay key, no hay overlays. */
  bySymbol: Record<string, OverlayBundle>

  /** Modo minimalista global: oculta structure (S/R + pivots) del chart,
   *  manteniendo EMAs y TradeIdea. Persistido entre sesiones. */
  minimalMode: boolean

  /** Reemplaza (no merge) las indicadores del símbolo. */
  setIndicators: (symbol: string, indicators: IndicatorOverlays) => void

  /** Merge: añade EMAs/SMAs sin pisar las existentes. Útil para acumular
   *  varias herramientas (confluence + indicators) en la misma sesión. */
  mergeIndicators: (symbol: string, partial: Partial<IndicatorOverlays>) => void

  setStructure: (symbol: string, structure: StructureOverlays | null) => void
  /** Reemplaza el array completo de tradeIdeas. Lo usa el setup-bridge
   *  cuando refetch los pending/active de DB. */
  setTradeIdeas: (symbol: string, ideas: TradeIdeaOverlay[]) => void
  /** Upsert por id — añade el idea si no existe, lo reemplaza si ya. Lo
   *  usa el chat-bridge para inyección optimista antes de persistir. */
  addTradeIdea: (symbol: string, idea: TradeIdeaOverlay) => void
  /** Quita por id. Útil para futuras (manual dismiss). */
  removeTradeIdea: (symbol: string, id: string) => void

  /** Toggle individual de cada capa — usado por OverlayPanel UI. */
  toggleEma: (symbol: string, period: number) => void
  toggleSma: (symbol: string, period: number) => void
  toggleBbands: (symbol: string) => void
  toggleVwap: (symbol: string) => void

  toggleMinimalMode: () => void

  /** Borra estado del agente (structure + tradeIdea) pero conserva
   *  indicators del usuario. Útil cuando el chat sesiona se renueva. */
  clearAgent: (symbol: string) => void
  clear: (symbol: string) => void
  clearAll: () => void
}

function ensureBundle(
  state: { bySymbol: Record<string, OverlayBundle> },
  symbol: string,
): OverlayBundle {
  const cur = state.bySymbol[symbol]
  if (!cur) return { ...EMPTY_BUNDLE }
  // Defensive merge: si el bundle persistido viene de una versión antigua
  // y le faltan keys nuevas, el spread de EMPTY_BUNDLE primero garantiza
  // valores por defecto.
  return { ...EMPTY_BUNDLE, ...cur }
}

export const useChartOverlays = create<ChartOverlaysState>()(
  persist(
    (set) => ({
      bySymbol: {},
      minimalMode: false,

  setIndicators: (symbol, indicators) =>
    set((state) => ({
      bySymbol: {
        ...state.bySymbol,
        [symbol]: { ...ensureBundle(state, symbol), indicators },
      },
    })),

  mergeIndicators: (symbol, partial) =>
    set((state) => {
      const cur = ensureBundle(state, symbol).indicators
      const merged: IndicatorOverlays = {
        ema: partial.ema
          ? Array.from(new Set([...cur.ema, ...partial.ema])).sort((a, b) => a - b)
          : cur.ema,
        sma: partial.sma
          ? Array.from(new Set([...cur.sma, ...partial.sma])).sort((a, b) => a - b)
          : cur.sma,
        bbands: partial.bbands ?? cur.bbands,
        vwap: partial.vwap ?? cur.vwap,
      }
      return {
        bySymbol: {
          ...state.bySymbol,
          [symbol]: { ...ensureBundle(state, symbol), indicators: merged },
        },
      }
    }),

  setStructure: (symbol, structure) =>
    set((state) => ({
      bySymbol: {
        ...state.bySymbol,
        [symbol]: { ...ensureBundle(state, symbol), structure },
      },
    })),

  setTradeIdeas: (symbol, ideas) =>
    set((state) => {
      const enriched = ideas.map((idea) => ({
        ...idea,
        proposedAtSec:
          idea.proposedAtSec ?? Math.floor(Date.now() / 1000),
      }))
      return {
        bySymbol: {
          ...state.bySymbol,
          [symbol]: { ...ensureBundle(state, symbol), tradeIdeas: enriched },
        },
      }
    }),

  addTradeIdea: (symbol, idea) =>
    set((state) => {
      const cur = ensureBundle(state, symbol)
      const enriched: TradeIdeaOverlay = {
        ...idea,
        proposedAtSec:
          idea.proposedAtSec ?? Math.floor(Date.now() / 1000),
      }
      // Upsert por id: si ya existe, reemplazo; si no, append.
      const idx = cur.tradeIdeas.findIndex((i) => i.id === enriched.id)
      const nextIdeas =
        idx >= 0
          ? cur.tradeIdeas.map((i, n) => (n === idx ? enriched : i))
          : [...cur.tradeIdeas, enriched]
      return {
        bySymbol: {
          ...state.bySymbol,
          [symbol]: { ...cur, tradeIdeas: nextIdeas },
        },
      }
    }),

  removeTradeIdea: (symbol, id) =>
    set((state) => {
      const cur = ensureBundle(state, symbol)
      return {
        bySymbol: {
          ...state.bySymbol,
          [symbol]: {
            ...cur,
            tradeIdeas: cur.tradeIdeas.filter((i) => i.id !== id),
          },
        },
      }
    }),

  toggleEma: (symbol, period) =>
    set((state) => {
      const cur = ensureBundle(state, symbol)
      const next = cur.indicators.ema.includes(period)
        ? cur.indicators.ema.filter((p) => p !== period)
        : [...cur.indicators.ema, period].sort((a, b) => a - b)
      return {
        bySymbol: {
          ...state.bySymbol,
          [symbol]: { ...cur, indicators: { ...cur.indicators, ema: next } },
        },
      }
    }),

  toggleSma: (symbol, period) =>
    set((state) => {
      const cur = ensureBundle(state, symbol)
      const next = cur.indicators.sma.includes(period)
        ? cur.indicators.sma.filter((p) => p !== period)
        : [...cur.indicators.sma, period].sort((a, b) => a - b)
      return {
        bySymbol: {
          ...state.bySymbol,
          [symbol]: { ...cur, indicators: { ...cur.indicators, sma: next } },
        },
      }
    }),

  toggleBbands: (symbol) =>
    set((state) => {
      const cur = ensureBundle(state, symbol)
      return {
        bySymbol: {
          ...state.bySymbol,
          [symbol]: {
            ...cur,
            indicators: { ...cur.indicators, bbands: !cur.indicators.bbands },
          },
        },
      }
    }),

  toggleVwap: (symbol) =>
    set((state) => {
      const cur = ensureBundle(state, symbol)
      return {
        bySymbol: {
          ...state.bySymbol,
          [symbol]: {
            ...cur,
            indicators: { ...cur.indicators, vwap: !cur.indicators.vwap },
          },
        },
      }
    }),

  toggleMinimalMode: () => set((state) => ({ minimalMode: !state.minimalMode })),

  clearAgent: (symbol) =>
    set((state) => {
      const cur = state.bySymbol[symbol]
      if (!cur) return state
      return {
        bySymbol: {
          ...state.bySymbol,
          [symbol]: { ...cur, structure: null, tradeIdeas: [] },
        },
      }
    }),

  clear: (symbol) =>
    set((state) => {
      const next = { ...state.bySymbol }
      delete next[symbol]
      return { bySymbol: next }
    }),

  clearAll: () => set({ bySymbol: {} }),
    }),
    {
      name: "trading-copilot:chart-overlays",
      version: 2,
      // Sólo persistimos los `indicators` (preference user-level). Structure
      // y tradeIdeas son ephemerals del análisis del agente — refrescar la
      // página debe limpiarlos. minimalMode también persiste (preferencia
      // visual del usuario).
      partialize: (state) => ({
        minimalMode: state.minimalMode,
        bySymbol: Object.fromEntries(
          Object.entries(state.bySymbol).map(([sym, bundle]) => [
            sym,
            {
              indicators: bundle.indicators,
              structure: null,
              tradeIdeas: [],
            },
          ]),
        ),
      }),
      // v1 (singular `tradeIdea`) → v2 (array `tradeIdeas`). Los bundles
      // antiguos en localStorage tienen `tradeIdea: null` y carecen de
      // `tradeIdeas`. Sin esta migración, `countActiveLayers` y otras
      // lecturas fallan al acceder `.length` de undefined.
      migrate: (persisted: unknown, _version: number) => {
        if (!persisted || typeof persisted !== "object") return persisted
        const p = persisted as {
          bySymbol?: Record<string, unknown>
          minimalMode?: boolean
        }
        if (!p.bySymbol) return persisted
        const migratedBySymbol: Record<string, OverlayBundle> = {}
        for (const [sym, bundle] of Object.entries(p.bySymbol)) {
          const b = (bundle ?? {}) as Record<string, unknown>
          migratedBySymbol[sym] = {
            indicators:
              (b.indicators as IndicatorOverlays | undefined) ??
              EMPTY_INDICATORS,
            structure: null,
            tradeIdeas: [],
          }
        }
        return {
          ...p,
          bySymbol: migratedBySymbol,
        }
      },
    },
  ),
)

/** Cuenta total de capas activas para el badge "N capas" del OverlayPanel.
 *  Defensivo ante bundles malformados (p.ej. localStorage de versiones
 *  anteriores antes de que la migration corra). */
export function countActiveLayers(bundle: OverlayBundle | undefined): number {
  if (!bundle) return 0
  let n = 0
  n += bundle.indicators?.ema?.length ?? 0
  n += bundle.indicators?.sma?.length ?? 0
  if (bundle.indicators?.bbands) n += 1
  if (bundle.indicators?.vwap) n += 1
  if (bundle.structure) {
    n += (bundle.structure.support?.length ?? 0) +
      (bundle.structure.resistance?.length ?? 0)
    n += (bundle.structure.swingHighs?.length ?? 0) +
      (bundle.structure.swingLows?.length ?? 0)
  }
  n += bundle.tradeIdeas?.length ?? 0
  return n
}
