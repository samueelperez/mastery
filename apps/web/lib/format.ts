/** Project-wide formatting helpers — used by tables, cards, and feeds across
 * /research and /alerts. Keep these pure and side-effect-free; locale defaults
 * follow the user's browser. */

import type { AlertConditionDTO } from "@/lib/api"

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

/** "rsi_14 <= 30 Y ema_21 cross_above ema_55" — used by AlertList + AlertCreatedCard.
 *  Operadores y nombres de columna se quedan en EN (jargon canónico). */
export function summarizeAlertConditions(
  conds: AlertConditionDTO[],
  logic: "all" | "any",
): string {
  const parts = conds.map((c) => `${c.left} ${c.op} ${c.right}`)
  if (parts.length === 1) return parts[0]
  return parts.join(logic === "all" ? " Y " : " O ")
}
