"use client"

import { useQuery } from "@tanstack/react-query"
import { useEffect } from "react"

import { fetchSetups, type SetupListRowDTO } from "@/lib/core/api"
import type { Timeframe } from "@/lib/chat/types"
import { isWatchSymbol } from "@/lib/store/active-symbol"
import {
  useChartOverlays,
  type TradeIdeaOverlay,
} from "@/lib/store/chart-overlays"

const AGENT_TIMEFRAMES = new Set<Timeframe>(["15m", "1h", "4h", "1d"])

function asAgentTimeframe(tf: string): Timeframe | null {
  return AGENT_TIMEFRAMES.has(tf as Timeframe) ? (tf as Timeframe) : null
}

/** Hidrata `tradeIdeas[]` del store con TODOS los setups `pending`/`active`
 *  persistidos en DB para el `symbol` activo. El switcher del ChartLegend
 *  permite alternar entre ellos cuando hay varios.
 *
 *  Razón: el store excluye `tradeIdeas` de la persistencia local — son
 *  ephemerals derivados de DB. Sin este bridge, recargar la página
 *  perdería las zonas aunque los setups siguieran abiertos en
 *  `journal_trades`.
 *
 *  Coexistencia con `useSymbolBridge` (chat → store): el chat-bridge
 *  hace `addTradeIdea` con id `chat-${ts}` cuando llega un TradeIdea
 *  fresco. Este bridge fetcha cada 30s y hace `setTradeIdeas` con la
 *  verdad de DB — los placeholders `chat-*` se descartan automáticamente
 *  cuando el setup ya tiene su uuid persistido. Visualmente nada salta
 *  porque entry/SL/TP son los mismos.
 */
export function useActiveSetupBridge(symbol: string): void {
  const setTradeIdeas = useChartOverlays((s) => s.setTradeIdeas)

  const enabled = isWatchSymbol(symbol)
  const { data } = useQuery({
    queryKey: ["active-setups", symbol],
    queryFn: ({ signal }) =>
      fetchSetups({ symbol, source: "agent_proposal", signal }),
    enabled,
    staleTime: 10_000,
    // Cada 10s rechequea — si el scout propone un setup nuevo o el watcher
    // cierra uno, el chart pinta/despinta zonas con feedback loop tight
    // (A.8: bajado de 30s a 10s para acomodar mayor cadencia esperada con
    // scout autónomo en Fase C).
    refetchInterval: 10_000,
  })

  useEffect(() => {
    if (!enabled) return
    if (!data) return
    const overlays = mapOpenSetupsToOverlays(data.rows, symbol)
    setTradeIdeas(symbol, overlays)
  }, [data, symbol, enabled, setTradeIdeas])
}

/** Mapea los setups pending/active del símbolo a `TradeIdeaOverlay[]`.
 *  Filtros: status ∈ {pending, active}, mismo símbolo, timeframe válido,
 *  stop_loss_px no nulo. Se ordena por `proposed_at` desc para que el
 *  switcher arranque mostrando el más reciente. */
function mapOpenSetupsToOverlays(
  rows: SetupListRowDTO[],
  symbol: string,
): TradeIdeaOverlay[] {
  const upper = symbol.toUpperCase()
  const open = rows
    .filter((r) => r.status === "pending" || r.status === "active")
    .filter((r) => r.symbol === upper)
    .filter((r) => r.stop_loss_px !== null)
    .sort((a, b) => {
      const at = a.proposed_at ? new Date(a.proposed_at).getTime() : 0
      const bt = b.proposed_at ? new Date(b.proposed_at).getTime() : 0
      return bt - at
    })

  const out: TradeIdeaOverlay[] = []
  for (const row of open) {
    const tf = asAgentTimeframe(row.timeframe)
    if (!tf) continue
    const proposedAtSec = row.proposed_at
      ? Math.floor(new Date(row.proposed_at).getTime() / 1000)
      : Math.floor(Date.now() / 1000)
    out.push({
      id: row.id,
      direction: row.side,
      entry: row.entry_px,
      stopLoss: row.stop_loss_px!,
      targets: row.targets.map((t) => ({ label: t.label, price: t.price })),
      tf,
      proposedAtSec,
    })
  }
  return out
}
