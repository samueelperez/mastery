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

/** "12s ago" / "5m ago" / "2h ago" / "3d ago" — for live event feeds. */
export function formatTimeAgo(ts: string, now: Date = new Date()): string {
  const sec = Math.max(0, Math.floor((now.getTime() - new Date(ts).getTime()) / 1000))
  if (sec < 60) return `${sec}s ago`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}m ago`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr}h ago`
  return `${Math.floor(hr / 24)}d ago`
}

/** "rsi_14 <= 30 AND ema_21 cross_above ema_55" — used by AlertList + AlertCreatedCard. */
export function summarizeAlertConditions(
  conds: AlertConditionDTO[],
  logic: "all" | "any",
): string {
  const parts = conds.map((c) => `${c.left} ${c.op} ${c.right}`)
  if (parts.length === 1) return parts[0]
  return parts.join(logic === "all" ? " AND " : " OR ")
}
