/** Project-wide formatting helpers — used by tables, cards, and feeds across
 * /research and /alerts. Keep these pure and side-effect-free; locale defaults
 * follow the user's browser. */

import type { AlertConditionDTO } from "@/lib/core/api"

/** "May 4 14:30" — used by recent-events tables where date + 24h time both matter. */
export function formatShortDateTime(ts: string): string {
  const d = new Date(ts)
  const date = d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
  const time = d.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  })
  return `${date} ${time}`
}

/** "May 4 26" — used in journal entries where year matters more than time. */
export function formatShortDate(ts: string): string {
  const d = new Date(ts)
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "2-digit",
  })
}

/** "May 4" — used by Recharts tick labels. */
export function formatChartDate(ts: string | number): string {
  const d = new Date(ts)
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
}

/** "hace 12s" / "hace 5m" / "hace 2h" / "hace 3d" — for live event feeds. */
export function formatTimeAgo(ts: string, now: Date = new Date()): string {
  const sec = Math.max(0, Math.floor((now.getTime() - new Date(ts).getTime()) / 1000))
  if (sec < 60) return `hace ${sec}s`
  const min = Math.floor(sec / 60)
  if (min < 60) return `hace ${min}m`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `hace ${hr}h`
  return `hace ${Math.floor(hr / 24)}d`
}

/** Pretty-print de una condición DSL: traduce nombres técnicos de
 *  columna (rsi_14, ema_21, c) a etiquetas humanas (RSI(14), EMA21,
 *  Precio) y operadores a símbolos / castellano (≤, "cruza por encima
 *  de"). Usado por AlertCreatedCard, RuleCard y AlertEventFeed. */
export function summarizeAlertConditions(
  conds: AlertConditionDTO[],
  logic: "all" | "any",
): string {
  if (conds.length === 0) return "(sin condiciones)"
  const parts = conds.map(formatCondition)
  if (parts.length === 1) return parts[0]
  return parts.join(logic === "all" ? " Y " : " O ")
}

function formatCondition(c: AlertConditionDTO): string {
  const left = formatColumn(c.left)
  const op = formatOperator(c.op)
  const right =
    typeof c.right === "number" ? formatNumber(c.right) : formatColumn(c.right)
  // Cross operators usan sintaxis de frase ("X cruza por encima de Y").
  // Comparadores numéricos van en notación matemática ("X ≥ Y").
  if (c.op === "cross_above" || c.op === "cross_below") {
    return `${left} ${op} ${right}`
  }
  return `${left} ${op} ${right}`
}

function formatColumn(col: string): string {
  // OHLCV columnas base.
  const base: Record<string, string> = {
    c: "Precio",
    o: "Apertura",
    h: "Máximo",
    l: "Mínimo",
    v: "Volumen",
    bb_lower: "BB Lower",
    bb_mid: "BB Mid",
    bb_upper: "BB Upper",
    vwap: "VWAP",
    macd_line: "MACD",
    macd_signal: "MACD señal",
    macd_hist: "MACD hist",
  }
  if (base[col]) return base[col]

  // Indicators con length: rsi_14, ema_21, sma_50, atr_14, adx_14.
  // Convención: notación funcional para osciladores ("RSI(14)") y postfija
  // para medias móviles ("EMA21").
  const m = /^(rsi|ema|sma|atr|adx)_(\d+)$/.exec(col)
  if (m) {
    const [, name, length] = m
    const upper = name.toUpperCase()
    if (name === "ema" || name === "sma") return `${upper}${length}`
    return `${upper}(${length})`
  }
  // Fallback: dejar el nombre técnico tal cual.
  return col
}

function formatOperator(op: AlertConditionDTO["op"]): string {
  switch (op) {
    case "<=":
      return "≤"
    case ">=":
      return "≥"
    case "==":
      return "="
    case "<":
      return "<"
    case ">":
      return ">"
    case "cross_above":
      return "cruza por encima de"
    case "cross_below":
      return "cruza por debajo de"
  }
}

function formatNumber(n: number): string {
  // Decimales largos (e.g., 0.0001) se muestran completos; enteros sin
  // separador de miles para no inflar.
  if (Number.isInteger(n)) return String(n)
  return n.toLocaleString(undefined, { maximumFractionDigits: 4 })
}

/** "1h" / "30m" / "45s" / "sin cooldown". Usado por RuleCard + AlertCreatedCard. */
export function formatCooldown(seconds: number): string {
  if (seconds <= 0) return "sin cooldown"
  if (seconds >= 86400) {
    const d = Math.round(seconds / 86400)
    return `${d}d`
  }
  if (seconds >= 3600) {
    const h = Math.round(seconds / 3600)
    return `${h}h`
  }
  if (seconds >= 60) {
    const m = Math.round(seconds / 60)
    return `${m}m`
  }
  return `${seconds}s`
}
