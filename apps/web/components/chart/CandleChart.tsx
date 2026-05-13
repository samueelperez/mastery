"use client"

import {
  BaselineSeries,
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts"
import { useEffect, useRef } from "react"

import type { CandleDTO } from "@/lib/core/api"
import type {
  OverlayBundle,
  TradeIdeaOverlay,
} from "@/lib/store/chart-overlays"

import { readChartTokens, withAlpha, type ChartTokens } from "./overlays/colors"
import {
  computeBollinger,
  computeEma,
  computeSma,
  computeVwap,
  type LinePoint,
} from "./overlays/computeIndicators"
import type { LiveCandle } from "./useLiveCandles"

/**
 * Lightweight Charts wrapper.
 *
 * Performance rules:
 * - Chart and series API handles are kept in `useRef`, NOT `useState`. Each WS tick calls
 *   `series.update(candle)` directly without re-rendering React.
 * - Initial data goes in once via `series.setData()`, then ALL further mutations are deltas.
 * - Throttling is unnecessary at the React layer — Lightweight Charts repaints at most once
 *   per animation frame regardless of update rate.
 * - Resize handled with ResizeObserver via `autoSize: true` (no debounce needed).
 */
export interface CandleChartProps {
  initial: CandleDTO[] | undefined
  live: LiveCandle | null
  overlays?: OverlayBundle | null
  /** TradeIdea activa a renderizar (zona + price lines + marker). El padre
   *  resuelve cuál mostrar — el chart NO lee de `overlays.tradeIdeas[]`
   *  porque hay un switcher en `ChartLegend` que cambia la selección. */
  activeIdea?: TradeIdeaOverlay | null
  /** Timeframe activo del chart. Se usa para decidir si los swing pivots del
   *  structure aplican (sus timestamps están alineados al tf de su análisis). */
  activeTimeframe?: string
  /** Si true, oculta el structure (S/R + pivots) del chart. EMAs y
   *  TradeIdea siguen visibles. Toggle global del store. */
  minimalMode?: boolean
  className?: string
}

function toLwcCandle(c: CandleDTO) {
  return {
    time: (Math.floor(new Date(c.ts).getTime() / 1000) as unknown) as Time,
    open: c.o,
    high: c.h,
    low: c.l,
    close: c.c,
  }
}

/** Color asignado a cada periodo de EMA. Periodos no listados caen al
 *  fallback (gris). */
function emaColor(period: number, t: ChartTokens): string {
  if (period === 21) return t.amber
  if (period === 55) return t.violet
  if (period === 200) return t.fg3
  return t.fg2
}

function smaColor(period: number, t: ChartTokens): string {
  if (period === 50) return t.long
  if (period === 100) return t.violet
  return t.fg2
}

export function CandleChart({
  initial,
  live,
  overlays,
  activeIdea = null,
  activeTimeframe,
  minimalMode = false,
  className,
}: CandleChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null)
  const tokensRef = useRef<ChartTokens | null>(null)
  const seededRef = useRef(false)

  // Series de overlay indexadas por key (ej. "ema-21", "bbands-mid"). Se
  // recrean cada vez que cambia `overlays.indicators` o `initial`.
  const overlaySeriesRef = useRef<Map<string, ISeriesApi<"Line">>>(new Map())

  // Cleanup específico del trade idea (price lines + zone series).
  const tradeIdeaRef = useRef<{
    priceLines: IPriceLine[]
    zones: ISeriesApi<"Baseline">[]
  } | null>(null)
  // Cleanup específico de los niveles S/R del structure. LineSeries (no
  // priceLines) para que `autoscaleInfoProvider: () => null` impida que el
  // eje Y se desplace cuando una S o R cae fuera del rango de candles.
  const structureSeriesRef = useRef<ISeriesApi<"Line">[]>([])
  // Serie invisible que solo lleva whitespace data al futuro para extender
  // el time-axis. Sin esto, las BaselineSeries del setup pending con
  // timestamps post-última-vela se RENDERIZAN COMPRIMIDAS en el último
  // píxel del axis. NO podemos meter el whitespace en la candle series
  // porque rompe `update()` al llegar live ticks (los puntos del futuro
  // se vuelven el "último" → nuevos updates fallan con "Cannot update
  // oldest data").
  const axisExtenderRef = useRef<ISeriesApi<"Line"> | null>(null)
  // Markers compartidos: tradeIdea + structure swings se compilan en un solo
  // setMarkers() porque la API reemplaza, no mergea.
  const markersPluginRef = useRef<
    ISeriesMarkersPluginApi<Time> | null
  >(null)

  // Mount + unmount: create / destroy the chart instance.
  useEffect(() => {
    if (!containerRef.current) return
    const tokens = readChartTokens()
    tokensRef.current = tokens

    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: tokens.fg,
        fontFamily: "var(--font-sans)",
      },
      grid: {
        vertLines: { color: tokens.border, style: 1 },
        horzLines: { color: tokens.border, style: 1 },
      },
      rightPriceScale: { borderColor: tokens.border },
      timeScale: {
        borderColor: tokens.border,
        timeVisible: true,
        secondsVisible: false,
        // rightOffset reserva N velas de espacio en blanco al final del
        // viewport. Cuenta: 2 buffer (live candle visible) + 50 zona
        // pending + ~8 margen visual = 60. ⚠️ Solo surte efecto si NO
        // llamamos `fitContent()` — ese método reajusta el viewport para
        // que las velas quepan justas e ignora rightOffset. Por eso
        // usamos `scrollToRealTime()` en el seed effect.
        rightOffset: 60,
      },
      // Free crosshair — Normal mode lets the line follow the cursor anywhere
      // on the canvas (Magnet mode would snap horizontally to the nearest bar).
      crosshair: { mode: CrosshairMode.Normal },
    })

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: tokens.long,
      downColor: tokens.short,
      wickUpColor: tokens.long,
      wickDownColor: tokens.short,
      borderVisible: false,
    })

    // Serie invisible que solo extiende el time-axis con whitespace al
    // futuro. `lineVisible:false` oculta la línea, `priceLineVisible:false`
    // y `lastValueVisible:false` evitan ruido en el price scale, y
    // `autoscaleInfoProvider:()=>null` impide que afecte el rango Y.
    const axisExtender = chart.addSeries(LineSeries, {
      lineVisible: false,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      autoscaleInfoProvider: () => null,
    })

    chartRef.current = chart
    candleSeriesRef.current = candleSeries
    axisExtenderRef.current = axisExtender
    seededRef.current = false

    // Capturamos la ref dentro del effect para que el cleanup la cierre
    // correctamente — la regla react-hooks/exhaustive-deps avisa cuando
    // referenciamos `ref.current` directamente en el closure de cleanup.
    const overlaySeriesMap = overlaySeriesRef.current

    return () => {
      // El chart.remove() destruye también todas las series asociadas.
      chart.remove()
      chartRef.current = null
      candleSeriesRef.current = null
      axisExtenderRef.current = null
      overlaySeriesMap.clear()
      tradeIdeaRef.current = null
      markersPluginRef.current = null
      seededRef.current = false
    }
  }, [])

  // (Re-)seed con histórico cada vez que la referencia de `initial` cambia
  // (cambio de symbol/tf). `setData` REEMPLAZA el dataset.
  useEffect(() => {
    if (!candleSeriesRef.current) return
    if (!initial || initial.length === 0) {
      seededRef.current = false
      return
    }
    const candles = initial.map(toLwcCandle)
    candleSeriesRef.current.setData(candles)
    // Extendemos el time-axis al futuro con whitespace data (puntos solo
    // con `time`, sin OHLC) sobre la SERIE INVISIBLE `axisExtender`. Sin
    // esto, una BaselineSeries con timestamps posteriores a la última
    // vela se renderiza comprimida en el último píxel del axis. NO
    // metemos el whitespace en la candle series porque rompe `update()`
    // al llegar live ticks (los whitespace se vuelven el "último punto" →
    // el chart rebota nuevos ticks como "Cannot update oldest data").
    const lastIdx = initial.length - 1
    const lastTsSec = Math.floor(
      new Date(initial[lastIdx]!.ts).getTime() / 1000,
    )
    const prevTsSec =
      lastIdx >= 1
        ? Math.floor(new Date(initial[lastIdx - 1]!.ts).getTime() / 1000)
        : lastTsSec - 3600
    const spacingSec = Math.max(lastTsSec - prevTsSec, 60)
    const FUTURE_BARS = 80
    const futureWhitespace: { time: Time }[] = []
    for (let i = 1; i <= FUTURE_BARS; i++) {
      futureWhitespace.push({
        time: (lastTsSec + spacingSec * i) as unknown as Time,
      })
    }
    axisExtenderRef.current?.setData(
      futureWhitespace as unknown as Parameters<
        NonNullable<typeof axisExtenderRef.current>["setData"]
      >[0],
    )
    // `scrollToRealTime()` posiciona el viewport en la última vela REAL
    // RESPETANDO `rightOffset` — al contrario de `fitContent()` que
    // recalcula el viewport para que todos los datos quepan justos.
    chartRef.current?.timeScale().scrollToRealTime()
    seededRef.current = true
  }, [initial])

  // Aplicar cada WS tick como delta. Sin re-render React.
  useEffect(() => {
    if (!candleSeriesRef.current || !live || !seededRef.current) return
    candleSeriesRef.current.update(toLwcCandle(live))
  }, [live])

  // -------------------------------------------------------------------------
  // OVERLAY: indicadores (EMAs, SMAs, BB, VWAP)
  // -------------------------------------------------------------------------
  useEffect(() => {
    const chart = chartRef.current
    const tokens = tokensRef.current
    if (!chart || !tokens) return
    const seriesMap = overlaySeriesRef.current

    // Limpia las overlays anteriores antes de recalcular. La operación es
    // idempotente: aunque la prop no cambie, recreamos sólo si initial cambió
    // (porque las curvas dependen de los candles cargados). Ver dep array.
    for (const series of seriesMap.values()) {
      try {
        chart.removeSeries(series)
      } catch {
        /* el chart puede haberse desmontado entre frames */
      }
    }
    seriesMap.clear()

    const ind = overlays?.indicators
    if (!initial || initial.length === 0 || !ind) return

    const addLine = (
      key: string,
      data: LinePoint[],
      color: string,
      opts: { lineWidth?: 1 | 2; lineStyle?: LineStyle; title?: string } = {},
    ) => {
      if (data.length === 0) return
      const series = chart.addSeries(LineSeries, {
        color,
        lineWidth: opts.lineWidth ?? 1,
        lineStyle: opts.lineStyle ?? LineStyle.Solid,
        priceLineVisible: false,
        lastValueVisible: true,
        title: opts.title ?? key,
      })
      series.setData(data)
      seriesMap.set(key, series)
    }

    for (const period of ind.ema) {
      addLine(`ema-${period}`, computeEma(initial, period), emaColor(period, tokens), {
        lineWidth: 2,
        title: `EMA ${period}`,
      })
    }
    for (const period of ind.sma) {
      addLine(`sma-${period}`, computeSma(initial, period), smaColor(period, tokens), {
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        title: `SMA ${period}`,
      })
    }
    if (ind.bbands) {
      const bb = computeBollinger(initial, 20, 2)
      addLine("bb-mid", bb.mid, tokens.fg4, {
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        title: "BB mid",
      })
      addLine("bb-upper", bb.upper, tokens.fg4, {
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        title: "BB upper",
      })
      addLine("bb-lower", bb.lower, tokens.fg4, {
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        title: "BB lower",
      })
    }
    if (ind.vwap) {
      addLine("vwap", computeVwap(initial), tokens.long, {
        lineWidth: 1,
        title: "VWAP",
      })
    }
  }, [overlays?.indicators, initial])

  // -------------------------------------------------------------------------
  // OVERLAY: TradeIdea (entry / SL / TPs price lines + zonas BaselineSeries)
  // -------------------------------------------------------------------------
  useEffect(() => {
    const chart = chartRef.current
    const candleSeries = candleSeriesRef.current
    const tokens = tokensRef.current
    if (!chart || !candleSeries || !tokens) return

    // Cleanup de la TradeIdea previa.
    const prev = tradeIdeaRef.current
    if (prev) {
      for (const line of prev.priceLines) {
        try {
          candleSeries.removePriceLine(line)
        } catch {
          /* ignore — chart may have been unmounted */
        }
      }
      for (const zone of prev.zones) {
        try {
          chart.removeSeries(zone)
        } catch {
          /* ignore */
        }
      }
      tradeIdeaRef.current = null
    }

    const idea = activeIdea
    if (!idea || !initial || initial.length === 0) return

    // --- Price lines (entry / SL / TPs) -----------------------------------
    const priceLines: IPriceLine[] = []
    priceLines.push(
      candleSeries.createPriceLine({
        price: idea.entry,
        color: tokens.amber,
        lineStyle: LineStyle.Solid,
        lineWidth: 2,
        axisLabelVisible: true,
        title: `ENTRY ${idea.entry.toFixed(2)}`,
      }),
    )
    priceLines.push(
      candleSeries.createPriceLine({
        price: idea.stopLoss,
        color: tokens.short,
        lineStyle: LineStyle.Dashed,
        lineWidth: 2,
        axisLabelVisible: true,
        title: `SL ${idea.stopLoss.toFixed(2)}`,
      }),
    )
    for (const target of idea.targets) {
      priceLines.push(
        candleSeries.createPriceLine({
          price: target.price,
          color: tokens.long,
          lineStyle: LineStyle.Dashed,
          lineWidth: 1,
          axisLabelVisible: true,
          title: `${target.label} ${target.price.toFixed(2)}`,
        }),
      )
    }

    // --- Zonas (BaselineSeries con baseValue=entry) ------------------------
    // El span temporal de la zona depende del estado lógico del setup:
    //   - PENDING (precio aún no tocó entry desde proposedAt): zona se
    //     proyecta a 30 velas en el futuro y se desplaza a la derecha con
    //     cada vela nueva.
    //   - ACTIVE (entry tocado, en mercado): startTime se ancla en la vela
    //     del entry-hit; endTime es la última vela y crece horizontalmente.
    //   - CLOSED (SL o TP tocados): startTime y endTime quedan fijos en sus
    //     velas respectivas — la zona no se mueve más.
    // proposedAtSec es el reloj client-side capturado por el store al recibir
    // la idea, así no contamos velas históricas pre-propuesta como hits.
    const lastIdx = initial.length - 1
    const lastTsSec = Math.floor(new Date(initial[lastIdx]!.ts).getTime() / 1000)
    const prevTsSec =
      lastIdx >= 1
        ? Math.floor(new Date(initial[lastIdx - 1]!.ts).getTime() / 1000)
        : lastTsSec - 3600
    const candleSpacingSec = Math.max(lastTsSec - prevTsSec, 60)
    // Proyección 50 velas hacia el futuro — coincide con `rightOffset`
    // del time-scale para que la zona ocupe el espacio reservado entero.
    const projectionCandles = 50
    // BUFFER entre la última vela cerrada y el inicio de la zona pending.
    // La "siguiente vela" (lastTsSec + 1 spacing) suele ser la candle LIVE
    // en formación con price action activo. Si la zona se pinta encima
    // de esa candle, visualmente parece que el trade YA está abierto en
    // esa vela. Con buffer=2 dejamos la live a la vista y empezamos la
    // zona DESPUÉS de ella.
    const PENDING_GAP_CANDLES = 2
    const projectionStartSec =
      lastTsSec + candleSpacingSec * PENDING_GAP_CANDLES
    const proposedAtSec = idea.proposedAtSec ?? lastTsSec

    const tpPrices = idea.targets.map((t) => t.price)
    const isLong = idea.direction === "long"
    const candleAt = (i: number) => {
      const c = initial[i]!
      return {
        ts: Math.floor(new Date(c.ts).getTime() / 1000),
        h: c.h,
        l: c.l,
      }
    }
    // El "side de aproximación" del precio al entry depende del close en el
    // momento de la propuesta:
    //  - Long PULLBACK: precio actual SOBRE entry → hit cuando low <= entry.
    //  - Long BREAKOUT: precio actual BAJO entry → hit cuando high >= entry.
    //  - Short PULLBACK: precio actual BAJO entry → hit cuando high >= entry.
    //  - Short BREAKOUT: precio actual SOBRE entry → hit cuando low <= entry.
    //
    // Sin esta distinción, un long pullback (entry < precio) marcaba TODAS
    // las velas históricas como entry-hit (todas con high >= entry) → la
    // primera vela post-proposedAt quedaba anclada como "active", la zona
    // se pintaba sobre velas existentes y parecía abierta cuando NO lo está.
    const proposedCandleIdx = (() => {
      for (let i = 0; i <= lastIdx; i++) {
        if (candleAt(i).ts >= proposedAtSec) return i
      }
      return lastIdx
    })()
    const proposedClose = initial[proposedCandleIdx]!.c
    const isPullback = isLong
      ? proposedClose > idea.entry
      : proposedClose < idea.entry

    // Para LONG: pullback hit = low<=entry, breakout hit = high>=entry.
    // Para SHORT: pullback hit = high>=entry, breakout hit = low<=entry.
    const entryHits = (h: number, l: number) => {
      if (isLong) return isPullback ? l <= idea.entry : h >= idea.entry
      return isPullback ? h >= idea.entry : l <= idea.entry
    }

    // 1) Buscar entry-hit en velas con ts >= proposedAt.
    let entryHitIdx: number | null = null
    for (let i = 0; i <= lastIdx; i++) {
      const c = candleAt(i)
      if (c.ts < proposedAtSec) continue
      if (entryHits(c.h, c.l)) {
        entryHitIdx = i
        break
      }
    }

    // 2) Si hubo entry-hit, buscar close-hit (SL o algún TP) DESDE allí.
    let closeHitIdx: number | null = null
    if (entryHitIdx !== null) {
      for (let i = entryHitIdx; i <= lastIdx; i++) {
        const c = candleAt(i)
        // Long: SL = low <= stopLoss · TP = high >= tp
        // Short: SL = high >= stopLoss · TP = low <= tp
        const slHit = isLong ? c.l <= idea.stopLoss : c.h >= idea.stopLoss
        const tpHit = tpPrices.some((tp) =>
          isLong ? c.h >= tp : c.l <= tp,
        )
        if (slHit || tpHit) {
          closeHitIdx = i
          break
        }
      }
    }

    // Anchos mínimos visibles por estado — evitan zonas de 0-1 vela cuando
    // el entry-hit es muy reciente (active recién iniciado) o cuando entry
    // y close caen en velas adyacentes.
    const ACTIVE_MIN_WIDTH_CANDLES = 25
    const CLOSED_MIN_WIDTH_CANDLES = 5

    let startTimeSec: number
    let endTimeSec: number
    if (entryHitIdx === null) {
      // PENDING: proyectada al futuro DESPUÉS de la última vela cerrada.
      startTimeSec = projectionStartSec
      endTimeSec = projectionStartSec + candleSpacingSec * projectionCandles
    } else if (closeHitIdx === null) {
      // ACTIVE: anclada en entry-hit, crece con cada vela nueva. Si la
      // entry-hit es muy reciente, garantizamos al menos
      // ACTIVE_MIN_WIDTH_CANDLES candles de ancho proyectando al futuro
      // — sin esto el rectángulo sería de 1 vela y no se distinguiría.
      startTimeSec = candleAt(entryHitIdx).ts
      endTimeSec = Math.max(
        lastTsSec,
        startTimeSec + candleSpacingSec * ACTIVE_MIN_WIDTH_CANDLES,
      )
    } else {
      // CLOSED: ambos extremos congelados, con ancho mínimo defensivo
      // para que se vea aunque entry y close fueran en velas adyacentes.
      startTimeSec = candleAt(entryHitIdx).ts
      endTimeSec = candleAt(closeHitIdx).ts
      endTimeSec = Math.max(
        endTimeSec,
        startTimeSec + candleSpacingSec * CLOSED_MIN_WIDTH_CANDLES,
      )
    }
    const startTime = startTimeSec as unknown as Time
    const endTime = endTimeSec as unknown as Time

    const zones: ISeriesApi<"Baseline">[] = []
    zones.push(
      makeZoneSeries(chart, {
        baseValue: idea.entry,
        targetValue: idea.stopLoss,
        color: tokens.short,
        startTime,
        endTime,
      }),
    )
    if (idea.targets.length > 0) {
      const firstTarget = idea.targets[0]!
      zones.push(
        makeZoneSeries(chart, {
          baseValue: idea.entry,
          targetValue: firstTarget.price,
          color: tokens.long,
          startTime,
          endTime,
        }),
      )
    }

    tradeIdeaRef.current = { priceLines, zones }
  }, [activeIdea, initial])

  // -------------------------------------------------------------------------
  // OVERLAY: Structure (S/R como LineSeries horizontales que NO afectan al
  // autoscale del eje Y — clave para que el viewport no salte cuando llega
  // un nivel lejano).
  // -------------------------------------------------------------------------
  useEffect(() => {
    const chart = chartRef.current
    const candleSeries = candleSeriesRef.current
    const tokens = tokensRef.current
    if (!chart || !candleSeries || !tokens) return

    // Cleanup series anteriores.
    for (const series of structureSeriesRef.current) {
      try {
        chart.removeSeries(series)
      } catch {
        /* ignore */
      }
    }
    structureSeriesRef.current = []

    const structure = overlays?.structure
    if (!structure || !initial || initial.length === 0) return
    if (minimalMode) return

    // Sólo dibujamos los 3 niveles más cercanos al precio actual de cada
    // lado. Los otros viven en el store (el OverlayPanel los cuenta) pero
    // no contaminan visualmente. El "currentPrice" es el último cierre.
    const currentPrice = initial[initial.length - 1]!.c
    const closestN = <T extends { price: number }>(arr: T[], n: number): T[] =>
      [...arr].sort(
        (a, b) => Math.abs(a.price - currentPrice) - Math.abs(b.price - currentPrice),
      ).slice(0, n)

    const startTime = tsToTime(initial[0]!.ts)
    const endTime = tsToTime(initial[initial.length - 1]!.ts)

    const drawLevel = (price: number, color: string) => {
      const series = chart.addSeries(LineSeries, {
        color,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        // Esta opción es la clave: la serie no contribuye al cálculo del
        // rango del priceScale derecho, por lo que un nivel lejano no
        // empuja el eje Y.
        autoscaleInfoProvider: () => null,
      })
      series.setData([
        { time: startTime, value: price },
        { time: endTime, value: price },
      ])
      return series
    }

    const next: ISeriesApi<"Line">[] = []
    for (const lvl of closestN(structure.support, 3)) {
      next.push(drawLevel(lvl.price, withAlpha(tokens.long, 0.6)))
    }
    for (const lvl of closestN(structure.resistance, 3)) {
      next.push(drawLevel(lvl.price, withAlpha(tokens.short, 0.6)))
    }
    structureSeriesRef.current = next
  }, [overlays?.structure, initial, minimalMode])

  // -------------------------------------------------------------------------
  // OVERLAY: Markers compilados (tradeIdea direccional + structure pivots)
  // setMarkers() reemplaza la lista entera, así que recompilamos todo en
  // un solo effect.
  // -------------------------------------------------------------------------
  useEffect(() => {
    const candleSeries = candleSeriesRef.current
    const tokens = tokensRef.current
    if (!candleSeries || !tokens) return

    if (!markersPluginRef.current) {
      markersPluginRef.current = createSeriesMarkers(candleSeries)
    }
    const plugin = markersPluginRef.current

    if (!initial || initial.length === 0) {
      plugin.setMarkers([])
      return
    }

    const all: SeriesMarker<Time>[] = []

    // Marker direccional del tradeIdea (activo) en la última vela.
    const idea = activeIdea
    if (idea) {
      const endTime = tsToTime(initial[initial.length - 1]!.ts)
      all.push({
        time: endTime,
        position: idea.direction === "long" ? "belowBar" : "aboveBar",
        color: idea.direction === "long" ? tokens.long : tokens.short,
        shape: idea.direction === "long" ? "arrowUp" : "arrowDown",
        text: idea.direction === "long" ? "L" : "S",
      })
    }

    // Pivots del structure — sólo si el tf coincide con el del análisis,
    // y siempre que NO estemos en modo minimalista. Cap -6 cada lado.
    const structure = overlays?.structure
    if (
      structure &&
      activeTimeframe &&
      structure.tf === activeTimeframe &&
      !minimalMode
    ) {
      for (const swing of structure.swingHighs.slice(-6)) {
        all.push({
          time: tsToTime(swing.ts),
          position: "aboveBar",
          color: tokens.amber,
          shape: "circle",
          size: 0.8,
        })
      }
      for (const swing of structure.swingLows.slice(-6)) {
        all.push({
          time: tsToTime(swing.ts),
          position: "belowBar",
          color: tokens.violet,
          shape: "circle",
          size: 0.8,
        })
      }
    }

    // Markers must be sorted ascending by time per lightweight-charts contract.
    all.sort((a, b) => Number(a.time) - Number(b.time))
    plugin.setMarkers(all)
  }, [activeIdea, overlays?.structure, activeTimeframe, initial, minimalMode])

  return <div ref={containerRef} className={className} />
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function tsToTime(ts: string): Time {
  return Math.floor(new Date(ts).getTime() / 1000) as unknown as Time
}

interface ZoneOpts {
  baseValue: number
  targetValue: number
  color: string
  startTime: Time
  endTime: Time
}

/** Crea una BaselineSeries para representar la zona entre `baseValue` y
 *  `targetValue`. Decide automáticamente top vs bottom fill según si el
 *  target está encima o debajo del base. Las opciones del lado contrario
 *  se ponen transparentes. */
function makeZoneSeries(
  chart: IChartApi,
  opts: ZoneOpts,
): ISeriesApi<"Baseline"> {
  const targetIsAbove = opts.targetValue > opts.baseValue
  const series = chart.addSeries(BaselineSeries, {
    baseValue: { type: "price", price: opts.baseValue },
    topFillColor1: targetIsAbove ? withAlpha(opts.color, 0.28) : "rgba(0,0,0,0)",
    topFillColor2: targetIsAbove ? withAlpha(opts.color, 0.04) : "rgba(0,0,0,0)",
    bottomFillColor1: targetIsAbove
      ? "rgba(0,0,0,0)"
      : withAlpha(opts.color, 0.04),
    bottomFillColor2: targetIsAbove
      ? "rgba(0,0,0,0)"
      : withAlpha(opts.color, 0.28),
    topLineColor: "rgba(0,0,0,0)",
    bottomLineColor: "rgba(0,0,0,0)",
    priceLineVisible: false,
    lastValueVisible: false,
    // Las zonas no deben afectar el rango del priceScale: un SL ancho
    // sería catastrófico para el zoom de las velas.
    autoscaleInfoProvider: () => null,
  })
  series.setData([
    { time: opts.startTime, value: opts.targetValue },
    { time: opts.endTime, value: opts.targetValue },
  ])
  return series
}
