"use client"

import { useEffect, useRef, useState } from "react"
import type { UIMessage } from "ai"

import {
  isConfluenceMap,
  isIndicatorPanel,
  isMarketStructure,
  unwrapToolResult,
  type IndicatorSpecDTO,
  type ConfluenceMapDTO,
  type MarketStructureDTO,
  type IndicatorPanelDTO,
} from "@/lib/agent-outputs"
import {
  isBriefAnalysis,
  isTradeIdea,
  type TradeIdea,
  type Timeframe,
} from "@/lib/chat-types"
import {
  isTimeframe,
  isWatchSymbol,
  useActiveSymbol,
  WATCH_SYMBOLS,
} from "@/lib/store/active-symbol"

/** El agente sólo acepta 15m/1h/4h/1d como timeframe (TradeIdea, structure,
 *  indicators, confluence). El store de active-symbol permite 1m porque la
 *  sidebar lo expone — pero el agente nunca lo emite. */
function isAgentTimeframe(s: string): s is Timeframe {
  return s === "15m" || s === "1h" || s === "4h" || s === "1d"
}
import {
  useChartOverlays,
  type IndicatorOverlays,
  type StructureOverlays,
  type TradeIdeaOverlay,
} from "@/lib/store/chart-overlays"

/** Tools que llevan `symbol` en su input. Se usan para "el usuario está
 *  mirando este símbolo" (cambio de chart) y para overlays. `create_alert`
 *  y `journal_*` NO entran — pueden referirse a un símbolo distinto del
 *  que se quiere visualizar. */
const SYMBOL_BEARING_TOOLS = new Set([
  "get_ohlcv",
  "get_indicators",
  "get_multi_tf_confluence",
  "get_market_structure",
  "run_backtest",
  "compute_panel",
])

/** Tools que aportan overlays al chart. Subset de SYMBOL_BEARING_TOOLS. */
const OVERLAY_TOOLS = new Set([
  "get_indicators",
  "get_multi_tf_confluence",
  "get_market_structure",
])

interface ToolPartLike {
  type: string
  state?: string
  input?: Record<string, unknown>
  output?: unknown
  toolCallId?: string
  toolName?: string
}

/** Inspecciona el último mensaje del asistente:
 *   1) Si contiene un tool con `input.symbol` (o un `tool-final_result` con
 *      `isTradeIdea`), empuja el símbolo al store global activo.
 *   2) Si tiene tools del set OVERLAY_TOOLS con `output-available`, parsea
 *      el output y persiste las capas en `useChartOverlays`.
 *
 *  Si el símbolo NO está en la watchlist, devuelve `warning` para que el
 *  componente padre lo muestre como toast inline. El warning se autolimpia
 *  a los 4s. */
export function useSymbolBridge(messages: UIMessage[]) {
  const setBoth = useActiveSymbol((s) => s.setBoth)
  const activeSymbol = useActiveSymbol((s) => s.symbol)
  const overlays = useChartOverlays()
  const lastSymbolDecision = useRef<string>("")
  const lastOverlayDecision = useRef<string>("")
  const [warning, setWarning] = useState<string | null>(null)

  // 1) Symbol bridge: chart sigue al último tool relevante.
  useEffect(() => {
    if (!messages.length) return
    const last = messages[messages.length - 1]
    if (!last || last.role !== "assistant") return

    const result = findFocusedSymbol(last.parts)
    if (!result) return
    const { symbol, timeframe } = result
    const normalized = symbol.toUpperCase()
    const fingerprint = `${last.id}:${normalized}:${timeframe ?? ""}`
    if (fingerprint === lastSymbolDecision.current) return
    lastSymbolDecision.current = fingerprint

    if (!isWatchSymbol(normalized)) {
      setWarning(
        `${normalized} no está en la watchlist · añádelo a WATCH_SYMBOLS`,
      )
      return
    }
    if (normalized === activeSymbol && !timeframe) return
    const tfNormalized =
      timeframe && isTimeframe(timeframe) ? timeframe : undefined
    setBoth(normalized, tfNormalized)
  }, [messages, activeSymbol, setBoth])

  // 2) Overlay bridge: persistir capas extraídas de los tool outputs.
  useEffect(() => {
    if (!messages.length) return
    const last = messages[messages.length - 1]
    if (!last || last.role !== "assistant") return

    // Fingerprint sobre el message id + número de tool parts con output. Si
    // llega un tool nuevo o cambia el output, fingerprint cambia → re-extraemos.
    const overlayFp = `${last.id}:${countOverlayParts(last.parts)}`
    if (overlayFp === lastOverlayDecision.current) return
    lastOverlayDecision.current = overlayFp

    extractOverlays(last.parts, {
      mergeIndicators: overlays.mergeIndicators,
      setStructure: overlays.setStructure,
      addTradeIdea: overlays.addTradeIdea,
    })
  }, [messages, overlays.mergeIndicators, overlays.setStructure, overlays.addTradeIdea])

  // Auto-clear del warning a los 4s.
  useEffect(() => {
    if (!warning) return
    const id = setTimeout(() => setWarning(null), 4000)
    return () => clearTimeout(id)
  }, [warning])

  return { warning, dismissWarning: () => setWarning(null) }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function asToolPart(part: unknown): ToolPartLike | null {
  if (!part || typeof part !== "object") return null
  const p = part as ToolPartLike
  if (typeof p.type !== "string") return null
  return p
}

function findFocusedSymbol(
  parts: readonly unknown[],
): { symbol: string; timeframe?: string } | null {
  for (let i = parts.length - 1; i >= 0; i--) {
    const p = asToolPart(parts[i])
    if (!p) continue

    // Pydantic-AI con output_type union (BriefAnalysis | TradeIdea | str)
    // namespacea el tool: `tool-final_result_TradeIdea` /
    // `tool-final_result_BriefAnalysis`. El bridge debe aceptar ambos
    // prefijos para que el chart focusee el símbolo emitido.
    if (p.type.startsWith("tool-final_result")) {
      if (isTradeIdea(p.input)) {
        return { symbol: p.input.symbol, timeframe: p.input.timeframe }
      }
      if (isBriefAnalysis(p.input)) {
        return { symbol: p.input.symbol, timeframe: p.input.timeframe }
      }
      continue
    }

    let toolName: string | undefined
    if (p.type.startsWith("tool-")) toolName = p.type.replace(/^tool-/, "")
    else if (p.type === "dynamic-tool") toolName = p.toolName

    if (!toolName || !SYMBOL_BEARING_TOOLS.has(toolName)) continue
    if (p.state !== "output-available") continue
    const sym = p.input?.symbol
    if (typeof sym !== "string") continue
    const tf = p.input?.timeframe
    return { symbol: sym, timeframe: typeof tf === "string" ? tf : undefined }
  }
  return null
}

function countOverlayParts(parts: readonly unknown[]): number {
  let n = 0
  for (const part of parts) {
    const p = asToolPart(part)
    if (!p) continue
    if (p.state !== "output-available") continue
    if (p.type.startsWith("tool-final_result") && isTradeIdea(p.input)) {
      n += 1
      continue
    }
    let name: string | undefined
    if (p.type.startsWith("tool-")) name = p.type.replace(/^tool-/, "")
    else if (p.type === "dynamic-tool") name = p.toolName
    if (name && OVERLAY_TOOLS.has(name)) n += 1
  }
  return n
}

interface OverlaySetters {
  mergeIndicators: (symbol: string, partial: Partial<IndicatorOverlays>) => void
  setStructure: (symbol: string, structure: StructureOverlays | null) => void
  addTradeIdea: (symbol: string, idea: TradeIdeaOverlay) => void
}

function extractOverlays(
  parts: readonly unknown[],
  setters: OverlaySetters,
): void {
  for (const part of parts) {
    const p = asToolPart(part)
    if (!p) continue

    if (p.type.startsWith("tool-final_result") && isTradeIdea(p.input)) {
      const idea: TradeIdea = p.input
      const symbol = idea.symbol.toUpperCase()
      const tag = "[chart-bridge:tradeIdea]"
      if (!isWatchSymbol(symbol)) {
        console.debug(tag, "rejected: symbol not in watchlist", {
          symbol,
          watchlist: WATCH_SYMBOLS,
        })
        continue
      }
      if (idea.direction === "no_trade") {
        console.debug(tag, "rejected: direction=no_trade", { symbol })
        continue
      }
      if (idea.entry === null || idea.invalidation === null) {
        console.debug(tag, "rejected: entry|invalidation null", {
          symbol,
          entry: idea.entry,
          invalidation: idea.invalidation,
        })
        continue
      }
      // ID placeholder mientras el setup-bridge hace el siguiente refetch
      // y reemplaza por el uuid de DB. Usamos un timestamp con jitter por
      // si dos TradeIdeas se emiten en el mismo ms (improbable pero
      // defensivo).
      const placeholderId = `chat-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
      setters.addTradeIdea(symbol, {
        id: placeholderId,
        direction: idea.direction,
        entry: idea.entry,
        stopLoss: idea.invalidation,
        targets: idea.targets.map((t) => ({ label: t.label, price: t.price })),
        tf: idea.timeframe,
      })
      console.debug(tag, "accepted", {
        symbol,
        id: placeholderId,
        entry: idea.entry,
        sl: idea.invalidation,
        targets: idea.targets.length,
      })
      continue
    }

    if (p.state !== "output-available") continue

    let name: string | undefined
    if (p.type.startsWith("tool-")) name = p.type.replace(/^tool-/, "")
    else if (p.type === "dynamic-tool") name = p.toolName
    if (!name || !OVERLAY_TOOLS.has(name)) continue

    const symbolRaw = p.input?.symbol
    if (typeof symbolRaw !== "string") continue
    const symbol = symbolRaw.toUpperCase()
    if (!isWatchSymbol(symbol)) continue

    const tfRaw = p.input?.timeframe
    const tf =
      typeof tfRaw === "string" && isAgentTimeframe(tfRaw) ? tfRaw : null

    if (name === "get_indicators") {
      const inputIndicators = p.input?.indicators
      if (Array.isArray(inputIndicators)) {
        applyIndicatorSpecs(symbol, inputIndicators as IndicatorSpecDTO[], setters)
      } else {
        // Fallback: inferir del output.
        const data = unwrapToolResult<IndicatorPanelDTO>(p.output)
        if (data && isIndicatorPanel(data)) {
          applyIndicatorPanel(symbol, data, setters)
        }
      }
      continue
    }

    if (name === "get_multi_tf_confluence") {
      const data = unwrapToolResult<ConfluenceMapDTO>(p.output)
      if (data && isConfluenceMap(data)) {
        // El tool computa siempre EMA 21/55/200 en todos los TFs pedidos.
        setters.mergeIndicators(symbol, { ema: [21, 55, 200] })
      }
      continue
    }

    if (name === "get_market_structure") {
      const data = unwrapToolResult<MarketStructureDTO>(p.output)
      if (!data || !isMarketStructure(data)) continue
      if (!tf) continue
      setters.setStructure(symbol, {
        tf,
        asOfTs: data.swing_highs[data.swing_highs.length - 1]?.ts ?? "",
        swingHighs: data.swing_highs.map((s) => ({ ts: s.ts, price: s.price })),
        swingLows: data.swing_lows.map((s) => ({ ts: s.ts, price: s.price })),
        support: data.support.map((l) => ({ price: l.price, touches: l.touches })),
        resistance: data.resistance.map((l) => ({
          price: l.price,
          touches: l.touches,
        })),
        trendLabel: data.trend_label,
      })
    }
  }
}

/** Convierte la lista IndicatorSpec[] que el agente PIDIÓ en flags del store.
 *  Esto es más fiable que leer el output (los keys son `ema_21`, hay que
 *  parsearlos con regex). El input siempre viene con name+length explícitos. */
function applyIndicatorSpecs(
  symbol: string,
  specs: IndicatorSpecDTO[],
  setters: OverlaySetters,
): void {
  const partial: Partial<IndicatorOverlays> = {}
  for (const spec of specs) {
    if (!spec || typeof spec.name !== "string") continue
    const length = spec.length ?? defaultLengthFor(spec.name)
    switch (spec.name) {
      case "ema":
        partial.ema = [...(partial.ema ?? []), length]
        break
      case "sma":
        partial.sma = [...(partial.sma ?? []), length]
        break
      case "bbands":
        partial.bbands = true
        break
      case "vwap":
        partial.vwap = true
        break
      // rsi/atr/macd/adx — info-only, no se dibujan en el chart principal.
    }
  }
  if (Object.keys(partial).length > 0) {
    setters.mergeIndicators(symbol, partial)
  }
}

/** Fallback cuando el input del tool no es accesible: parsear keys del
 *  IndicatorPanel.latest (e.g. "ema_21" → ema period 21). */
function applyIndicatorPanel(
  symbol: string,
  panel: IndicatorPanelDTO,
  setters: OverlaySetters,
): void {
  const partial: Partial<IndicatorOverlays> = { ema: [], sma: [] }
  for (const key of Object.keys(panel.latest)) {
    const m = key.match(/^(ema|sma)_(\d+)$/)
    if (m && m[1] && m[2]) {
      const period = parseInt(m[2], 10)
      if (m[1] === "ema") partial.ema!.push(period)
      else partial.sma!.push(period)
      continue
    }
    if (key === "bbands") partial.bbands = true
    else if (key === "vwap") partial.vwap = true
  }
  setters.mergeIndicators(symbol, partial)
}

function defaultLengthFor(name: string): number {
  switch (name) {
    case "ema":
      return 21
    case "sma":
      return 20
    case "rsi":
    case "atr":
    case "adx":
      return 14
    case "bbands":
      return 20
    default:
      return 14
  }
}
