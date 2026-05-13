/**
 * Cold→hot gradient for the liquidation heatmap, modelled after the
 * TradingDifferent visualisation. Five perceptual stops over the unit
 * interval; intensity comes from a log-normalised volume so a single
 * outlier doesn't compress every other zone to transparency.
 *
 * Long-liq and short-liq use the same gradient — the side is implicit in
 * the zone's position vs `current_price` (above = short-liq, below =
 * long-liq). This matches TradingDifferent (single hue for both).
 *
 * Returned strings are `rgba(...)` so the caller can drop them directly
 * into `ctx.fillStyle` without further parsing.
 */

// Five perceptual stops anchored at [0, 0.25, 0.5, 0.75, 1.0].
// Stops are sRGB so they paint exactly the same regardless of the
// browser's colour-management quirks.
const STOPS: readonly { t: number; r: number; g: number; b: number }[] = [
  { t: 0.0, r: 8, g: 16, b: 36 }, // deep navy — cold (low / no activity)
  { t: 0.25, r: 16, g: 96, b: 168 }, // cyan-blue — building cluster
  { t: 0.5, r: 230, g: 184, b: 40 }, // yellow — meaningful cluster
  { t: 0.75, r: 235, g: 110, b: 36 }, // orange — heavy cluster
  { t: 1.0, r: 226, g: 50, b: 50 }, // crimson — hot zone
]

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t
}

function rgbAt(intensity: number): { r: number; g: number; b: number } {
  if (intensity <= STOPS[0]!.t) return STOPS[0]!
  if (intensity >= STOPS[STOPS.length - 1]!.t) return STOPS[STOPS.length - 1]!
  for (let i = 0; i < STOPS.length - 1; i++) {
    const lo = STOPS[i]!
    const hi = STOPS[i + 1]!
    if (intensity >= lo.t && intensity <= hi.t) {
      const span = hi.t - lo.t
      const t = span === 0 ? 0 : (intensity - lo.t) / span
      return {
        r: Math.round(lerp(lo.r, hi.r, t)),
        g: Math.round(lerp(lo.g, hi.g, t)),
        b: Math.round(lerp(lo.b, hi.b, t)),
      }
    }
  }
  return STOPS[0]!
}

/**
 * Returns the alpha to paint a zone with, given its volume relative to
 * the median volume across the snapshot batch. Logarithmic so outliers
 * don't collapse the gradient. Clamped to `[0.06, 0.95]` so even the
 * smallest zones are barely visible and the hottest ones never fully
 * obscure the candles below.
 */
export function intensityAlpha(volumeUsd: number, medianVolumeUsd: number): number {
  if (!Number.isFinite(volumeUsd) || volumeUsd <= 0) return 0
  if (!Number.isFinite(medianVolumeUsd) || medianVolumeUsd <= 0) {
    return Math.min(0.95, Math.max(0.06, Math.log10(volumeUsd) / 8))
  }
  const ratio = volumeUsd / medianVolumeUsd
  // log10(1) = 0 (median) → 0.4; log10(10) = 1 (10× median) → 0.8; etc.
  const t = 0.4 + Math.log10(ratio) * 0.4
  return Math.min(0.95, Math.max(0.06, t))
}

/**
 * Convert an intensity in `[0, 1]` to a CSS rgba string with the given
 * alpha. The intensity drives the RGB rampa; alpha is separate so the
 * caller can modulate it (minimal mode, breathing, stale state).
 */
export function colorScaleRgba(intensity: number, alpha: number): string {
  const clamped = Math.min(1, Math.max(0, intensity))
  const { r, g, b } = rgbAt(clamped)
  const a = Math.min(1, Math.max(0, alpha))
  return `rgba(${r}, ${g}, ${b}, ${a.toFixed(3)})`
}

/**
 * Convenience: one-shot from volume → rgba, using both the log-normalised
 * alpha and the same value as the gradient intensity (so the hottest
 * zones are simultaneously the most opaque and the reddest — the two
 * channels reinforce each other).
 */
export function volumeToRgba(
  volumeUsd: number,
  medianVolumeUsd: number,
): string {
  const a = intensityAlpha(volumeUsd, medianVolumeUsd)
  // Re-map alpha [0.06, 0.95] to intensity [0, 1] so the hottest zones
  // get the crimson at the top of the rampa.
  const intensity = Math.min(1, Math.max(0, (a - 0.06) / (0.95 - 0.06)))
  return colorScaleRgba(intensity, a)
}

/**
 * Stops exposed for the colour-scale legend component in HM-PR3. The
 * indices are stable; UI binds to them by position.
 */
export const HEATMAP_GRADIENT_STOPS = STOPS.map(({ r, g, b }) => `rgb(${r}, ${g}, ${b})`)
