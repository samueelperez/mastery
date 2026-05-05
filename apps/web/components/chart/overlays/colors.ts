/** Lightweight-charts no parsea oklch() ni lab() directamente. Browsers
 *  normalizan las CSS vars a lab() en getComputedStyle, lo que rompe el
 *  parser. La solución fiable: rasterizar 1×1 px en un canvas y leer los
 *  bytes RGBA — siempre vienen en sRGB. */

let probeCtx: CanvasRenderingContext2D | null = null

function getProbe(): CanvasRenderingContext2D | null {
  if (probeCtx !== null) return probeCtx
  if (typeof document === "undefined") return null
  const canvas = document.createElement("canvas")
  canvas.width = 1
  canvas.height = 1
  probeCtx = canvas.getContext("2d", { willReadFrequently: true })
  return probeCtx
}

/** Convierte cualquier CSS color (incluido `oklch(...)`, `var(--xxx)`
 *  ya resuelto) a `#rrggbb` o `rgba(r,g,b,a)`. Si falla, retorna fallback. */
export function cssToRgb(cssValue: string, fallback: string): string {
  const probe = getProbe()
  if (!probe) return fallback
  probe.clearRect(0, 0, 1, 1)
  try {
    probe.fillStyle = cssValue
  } catch {
    return fallback
  }
  probe.fillRect(0, 0, 1, 1)
  const [r, g, b, a] = probe.getImageData(0, 0, 1, 1).data
  if (a === 255) {
    return `#${[r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("")}`
  }
  return `rgba(${r}, ${g}, ${b}, ${(a / 255).toFixed(3)})`
}

/** Lee un CSS custom property del :root (e.g. `--amber`) y lo convierte
 *  a sRGB hex/rgba. Si la variable no existe o no parsea, devuelve
 *  `fallback`. */
export function tokenRgb(name: string, fallback: string): string {
  if (typeof document === "undefined") return fallback
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim()
  return cssToRgb(value || fallback, fallback)
}

/** Devuelve `rgba(r,g,b,alpha)` a partir de un hex/rgba sRGB. Útil para
 *  zonas semitransparentes (SL/TP) donde necesitamos el mismo hue base
 *  con distintas alphas. */
export function withAlpha(rgb: string, alpha: number): string {
  // hex shortcut
  if (rgb.startsWith("#")) {
    const hex = rgb.slice(1)
    const r = parseInt(hex.slice(0, 2), 16)
    const g = parseInt(hex.slice(2, 4), 16)
    const b = parseInt(hex.slice(4, 6), 16)
    return `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(3)})`
  }
  // rgba(...) — sustituir alpha
  const m = rgb.match(/^rgba?\(([^)]+)\)/)
  if (m && m[1]) {
    const parts = m[1].split(",").map((s) => s.trim())
    if (parts.length >= 3) {
      return `rgba(${parts[0]}, ${parts[1]}, ${parts[2]}, ${alpha.toFixed(3)})`
    }
  }
  return rgb
}

/** Paleta semántica resuelta a sRGB. Llamar una vez por mount del chart. */
export interface ChartTokens {
  fg: string
  fg2: string
  fg3: string
  fg4: string
  border: string
  amber: string
  violet: string
  long: string
  short: string
}

export function readChartTokens(): ChartTokens {
  return {
    fg: tokenRgb("--fg", "#f8fafc"),
    fg2: tokenRgb("--fg-2", "#94a3b8"),
    fg3: tokenRgb("--fg-3", "#64748b"),
    fg4: tokenRgb("--fg-4", "#475569"),
    border: tokenRgb("--line", "#334155"),
    amber: tokenRgb("--amber", "#f59e0b"),
    violet: tokenRgb("--violet", "#8b5cf6"),
    long: tokenRgb("--long", "#10b981"),
    short: tokenRgb("--short", "#ef4444"),
  }
}
