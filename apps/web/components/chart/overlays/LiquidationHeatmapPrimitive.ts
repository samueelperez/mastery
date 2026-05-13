/**
 * 2D liquidation-heatmap primitive for lightweight-charts v5.
 *
 * Renders the TradingDifferent-style cloud: each `(snapshot_ts, zone)` is
 * a single `fillRect` whose width spans the snapshot interval and whose
 * height spans `[price_low, price_high]`. Colour intensity comes from
 * `heatmapColorScale.volumeToRgba()` driven by the volume's log-distance
 * from the snapshot batch median.
 *
 * One primitive attached to the candle series via
 * `series.attachPrimitive(primitive)`. Lifecycle:
 *   - `attached({chart, series, requestUpdate})`: capture refs for
 *     coordinate conversions and the redraw trigger.
 *   - `setData(snapshots)`: replace the dataset and call `requestUpdate`.
 *   - chart `chart.remove()` (CandleChart unmount) cleans this up for
 *     free — no explicit `detachPrimitive` needed in the React effect.
 *
 * `zOrder='bottom'` puts the heatmap *below* candles + EMAs + trade-idea
 * baseline, so the cloud is context and never obscures the price action.
 */

import type {
  IChartApi,
  ISeriesApi,
  ISeriesPrimitive,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  Logical,
  SeriesAttachedParameter,
  PrimitivePaneViewZOrder,
  Time,
} from "lightweight-charts"

import { volumeToRgba } from "./heatmapColorScale"

export interface HeatmapZone {
  price_low: number
  price_high: number
  side: "long_liq" | "short_liq"
  est_volume_usd: number
  source_breakdown?: Record<string, number>
  confidence?: "low" | "medium" | "high"
}

export interface HeatmapSnapshot {
  /** ISO-8601 UTC, e.g. `"2026-05-13T10:00:00+00:00"`. */
  ts: string
  zones: HeatmapZone[]
}

interface PrimitiveOptions {
  /** Global alpha multiplier applied AFTER per-zone alpha — 1.0 normal,
   *  0.5 minimal mode, 0.5 stale state. */
  alphaScale: number
  /** Width per snapshot column in seconds. The scheduler cadence is
   *  120s so each rect spans 120s of x. Caller passes this so we don't
   *  duplicate the constant on the FE side; the value comes from the
   *  history response or a sensible default. */
  snapshotIntervalSec: number
}

type MaybeNumber = number | null

export class LiquidationHeatmapPrimitive
  implements ISeriesPrimitive<Time>
{
  private _chart: IChartApi | null = null
  private _series: ISeriesApi<"Candlestick", Time> | null = null
  private _requestUpdate: (() => void) | null = null
  private _snapshots: HeatmapSnapshot[] = []
  private _medianVolume = 1
  private _citedPrices: readonly number[] = []
  private _citedHighlightColor = "rgba(245, 195, 80, 0.95)"
  private _options: PrimitiveOptions = {
    alphaScale: 1,
    snapshotIntervalSec: 120,
  }
  private _paneViews: [HeatmapPaneView]

  constructor() {
    this._paneViews = [new HeatmapPaneView(this)]
  }

  // ---------------- ISeriesPrimitive ----------------

  attached(param: SeriesAttachedParameter<Time>): void {
    this._chart = param.chart as IChartApi
    this._series = param.series as ISeriesApi<"Candlestick", Time>
    this._requestUpdate = param.requestUpdate
  }

  detached(): void {
    this._chart = null
    this._series = null
    this._requestUpdate = null
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this._paneViews
  }

  updateAllViews(): void {
    // No per-view state to recompute — draw() reads from this primitive
    // directly each frame.
  }

  // ---------------- Public API ----------------

  setData(snapshots: HeatmapSnapshot[]): void {
    this._snapshots = snapshots
    this._medianVolume = computeMedianVolume(snapshots)
    this._requestUpdate?.()
  }

  setOptions(partial: Partial<PrimitiveOptions>): void {
    this._options = { ...this._options, ...partial }
    this._requestUpdate?.()
  }

  /** Set the price levels (typically `idea.stop_loss` + `idea.targets[].price`)
   *  that should be visually highlighted because the active TradeIdea cites
   *  a zone containing them. Pass an empty array to clear. */
  setCitedPrices(prices: readonly number[], color?: string): void {
    this._citedPrices = prices
    if (color) this._citedHighlightColor = color
    this._requestUpdate?.()
  }

  // ---------------- Internals (used by HeatmapPaneView) ----------------

  get snapshots(): readonly HeatmapSnapshot[] {
    return this._snapshots
  }

  get medianVolume(): number {
    return this._medianVolume
  }

  get options(): PrimitiveOptions {
    return this._options
  }

  get citedPrices(): readonly number[] {
    return this._citedPrices
  }

  get citedHighlightColor(): string {
    return this._citedHighlightColor
  }

  priceToCoordinate(price: number): MaybeNumber {
    if (!this._series) return null
    const y = this._series.priceToCoordinate(price)
    return typeof y === "number" ? y : null
  }

  timeToCoordinate(tsIso: string): MaybeNumber {
    if (!this._chart) return null
    const tsSec = Math.floor(new Date(tsIso).getTime() / 1000)
    const ts = this._chart.timeScale()
    const visibleRange = ts.getVisibleRange()
    const visibleLogical = ts.getVisibleLogicalRange()
    if (!visibleRange || !visibleLogical) return null
    const fromSec = Number(visibleRange.from)
    const toSec = Number(visibleRange.to)
    if (!Number.isFinite(fromSec) || !Number.isFinite(toSec) || toSec <= fromSec) {
      return null
    }
    // Skip snapshots whose time falls outside the visible window — they
    // would otherwise clamp to the canvas edge and pile up as a vertical
    // line. Caller treats null as "off screen, don't draw".
    // Skip timestamps outside the visible window — otherwise the
    // out-of-range result would clamp to the canvas edge and pile up
    // as a vertical line at x=0. Caller treats null as "off-screen,
    // don't draw".
    if (tsSec < fromSec || tsSec > toSec) return null
    const logicalSpan = visibleLogical.to - visibleLogical.from
    const timeSpan = toSec - fromSec
    if (timeSpan <= 0 || logicalSpan <= 0) return null
    const fractional =
      visibleLogical.from + ((tsSec - fromSec) / timeSpan) * logicalSpan
    const x = ts.logicalToCoordinate(fractional as Logical)
    if (typeof x !== "number" || !Number.isFinite(x)) return null
    return x
  }

  /**
   * Find the zone at media-space coordinates `(x, y)`. Returns `null`
   * when no zone is hit — used by the React tooltip overlay subscribed
   * to `chart.subscribeCrosshairMove`. `null` also when the chart isn't
   * attached yet.
   */
  hitTestAt(
    x: number,
    y: number,
  ): { snapshot: HeatmapSnapshot; zone: HeatmapZone } | null {
    if (!this._chart || !this._series) return null
    const price = this._series.coordinateToPrice(y)
    if (price == null) return null
    const numericPrice = Number(price)
    const tsSec = this._chart.timeScale().coordinateToTime(x)
    if (tsSec == null) return null
    const tsMs = Number(tsSec) * 1000
    const intervalMs = this._options.snapshotIntervalSec * 1000

    // Find the snapshot whose interval contains `tsMs`. Snapshots are
    // sorted ascending; we scan from the back because mid-air hovers
    // are most likely on recent data.
    for (let i = this._snapshots.length - 1; i >= 0; i--) {
      const snap = this._snapshots[i]!
      const snapMs = new Date(snap.ts).getTime()
      if (tsMs < snapMs - intervalMs / 2) continue
      if (tsMs > snapMs + intervalMs * 1.5) return null
      for (const zone of snap.zones) {
        if (numericPrice >= zone.price_low && numericPrice <= zone.price_high) {
          return { snapshot: snap, zone }
        }
      }
      // We're inside this snapshot's time band but no zone matches the
      // price — return null rather than searching neighbouring snapshots
      // (the hover would be over empty space).
      return null
    }
    return null
  }
}

class HeatmapPaneView implements IPrimitivePaneView {
  constructor(private readonly _src: LiquidationHeatmapPrimitive) {}

  renderer(): IPrimitivePaneRenderer {
    return new HeatmapPaneRenderer(this._src)
  }

  zOrder(): PrimitivePaneViewZOrder {
    return "bottom"
  }
}

class HeatmapPaneRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly _src: LiquidationHeatmapPrimitive) {}

  draw(target: {
    useBitmapCoordinateSpace: (
      cb: (scope: {
        context: CanvasRenderingContext2D
        horizontalPixelRatio: number
        verticalPixelRatio: number
        bitmapSize: { width: number; height: number }
      }) => void,
    ) => void
  }): void {
    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context
      const { snapshots, medianVolume, options, citedPrices, citedHighlightColor } = this._src
      if (snapshots.length === 0) return

      const intervalSec = options.snapshotIntervalSec
      const hx = scope.horizontalPixelRatio
      const vy = scope.verticalPixelRatio
      const alphaScale = options.alphaScale

      // We use globalAlpha for the snapshot-wide modulation so each zone
      // only needs to emit its own rgba(...) string once.
      ctx.save()
      for (const snap of snapshots) {
        const xStart = this._src.timeToCoordinate(snap.ts)
        if (xStart === null) continue
        const tsEndIso = new Date(
          new Date(snap.ts).getTime() + intervalSec * 1000,
        ).toISOString()
        const xEnd = this._src.timeToCoordinate(tsEndIso) ?? xStart + 1
        const xPx = Math.floor(xStart * hx)
        const wPx = Math.max(1, Math.ceil((xEnd - xStart) * hx))

        for (const zone of snap.zones) {
          const yLow = this._src.priceToCoordinate(zone.price_low)
          const yHigh = this._src.priceToCoordinate(zone.price_high)
          if (yLow === null || yHigh === null) continue
          // priceToCoordinate is monotone-decreasing in price → yHigh
          // (higher price) returns smaller pixel y. Use min/max so the
          // rect renders correctly in either order.
          const yTop = Math.floor(Math.min(yLow, yHigh) * vy)
          const yBot = Math.ceil(Math.max(yLow, yHigh) * vy)
          const hPx = Math.max(1, yBot - yTop)

          const rgba = volumeToRgba(zone.est_volume_usd, medianVolume)
          ctx.fillStyle = applyAlphaScale(rgba, alphaScale)
          ctx.fillRect(xPx, yTop, wPx, hPx)

          if (
            citedPrices.length > 0 &&
            isCitedZone(zone, citedPrices)
          ) {
            // Draw a 1.5px luminous border around the cited zone for
            // every snapshot it appears in — the cloud "remembers" the
            // levels the agent referenced.
            const borderWidth = Math.max(1, Math.round(1.5 * Math.min(hx, vy)))
            ctx.strokeStyle = citedHighlightColor
            ctx.lineWidth = borderWidth
            ctx.strokeRect(
              xPx + borderWidth / 2,
              yTop + borderWidth / 2,
              wPx - borderWidth,
              hPx - borderWidth,
            )
          }
        }
      }
      ctx.restore()
    })
  }
}

function isCitedZone(zone: HeatmapZone, citedPrices: readonly number[]): boolean {
  for (const price of citedPrices) {
    if (price >= zone.price_low && price <= zone.price_high) return true
  }
  return false
}

// ---------------- Helpers ----------------

function computeMedianVolume(snapshots: HeatmapSnapshot[]): number {
  const volumes: number[] = []
  for (const s of snapshots) {
    for (const z of s.zones) {
      if (Number.isFinite(z.est_volume_usd) && z.est_volume_usd > 0) {
        volumes.push(z.est_volume_usd)
      }
    }
  }
  if (volumes.length === 0) return 1
  volumes.sort((a, b) => a - b)
  const mid = Math.floor(volumes.length / 2)
  if (volumes.length % 2 === 1) return volumes[mid]!
  return (volumes[mid - 1]! + volumes[mid]!) / 2
}

/** Multiply the alpha channel of an `rgba(r,g,b,a)` string by `scale`.
 *  Falls through unchanged if the input isn't an rgba(...). */
function applyAlphaScale(rgba: string, scale: number): string {
  if (scale === 1) return rgba
  const m = rgba.match(/^rgba\(([^,]+),([^,]+),([^,]+),([^)]+)\)$/)
  if (!m) return rgba
  const a = Math.min(1, Math.max(0, parseFloat(m[4]!) * scale))
  return `rgba(${m[1]!.trim()}, ${m[2]!.trim()}, ${m[3]!.trim()}, ${a.toFixed(3)})`
}
