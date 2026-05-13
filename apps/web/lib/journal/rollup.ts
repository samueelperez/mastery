/**
 * Aggregates client-side para la página `/journal`.
 *
 * El backend NO expone un rollup all-user (solo `/strategies/winrate` por
 * `setup_tag`). Aquí computamos lo que el hero del Diario necesita a partir
 * del array completo que ya fetchamos para renderizar el feed: week stats,
 * streak actual, y bucketing por día.
 *
 * Funciones puras — fáciles de testear, sin estado React.
 */

import type { SetupListRowDTO } from "@/lib/core/api"

const DAY_MS = 24 * 60 * 60 * 1000
const WEEK_MS = 7 * DAY_MS
const MONTH_MS = 30 * DAY_MS

/** Devuelve el timestamp más reciente que indica "actividad" en el setup.
 *  Usado para sortear y bucketing. Cae al `created_at` (siempre presente). */
export function activityTsMs(row: SetupListRowDTO): number {
  const candidates = [
    row.closed_at,
    row.entry_hit_at,
    row.proposed_at,
    row.trade_ts,
    row.created_at,
  ]
  for (const ts of candidates) {
    if (ts) {
      const ms = Date.parse(ts)
      if (Number.isFinite(ms)) return ms
    }
  }
  return 0
}

/** Mismo día calendario en zona local. No comparamos solo ms because
 *  cruzar medianoche debería contar como "ayer", no "hoy hace 1m". */
function isSameLocalDay(a: number, b: number): boolean {
  const da = new Date(a)
  const db = new Date(b)
  return (
    da.getFullYear() === db.getFullYear() &&
    da.getMonth() === db.getMonth() &&
    da.getDate() === db.getDate()
  )
}

// -----------------------------------------------------------------------------
// Week stats
// -----------------------------------------------------------------------------

export interface WeekStats {
  /** Cerrados con r_multiple > 0 en últimos 7 días. */
  wins: number
  /** Cerrados con r_multiple ≤ 0 en últimos 7 días. */
  losses: number
  /** Suma de r_multiple para cerrados en últimos 7 días. */
  totalR: number
  /** wins / (wins + losses) × 100, o null si total = 0. */
  winRatePct: number | null
  /** wins + losses (cerrados con R no nulo). */
  total: number
}

/** Filtra por `closed_at` en últimos 7 días + `r_multiple` definido.
 *  Setups cancelados o que aún no cerraron no cuentan. */
export function computeWeekStats(rows: SetupListRowDTO[]): WeekStats {
  const cutoff = Date.now() - WEEK_MS
  let wins = 0
  let losses = 0
  let totalR = 0
  for (const r of rows) {
    if (r.status !== "closed") continue
    if (r.r_multiple === null) continue
    const closedMs = r.closed_at ? Date.parse(r.closed_at) : 0
    if (!Number.isFinite(closedMs) || closedMs < cutoff) continue
    if (r.r_multiple > 0) wins += 1
    else losses += 1
    totalR += r.r_multiple
  }
  const total = wins + losses
  const winRatePct = total > 0 ? (wins / total) * 100 : null
  return { wins, losses, totalR, winRatePct, total }
}

// -----------------------------------------------------------------------------
// Streak
// -----------------------------------------------------------------------------

export interface Streak {
  kind: "win" | "loss" | "none"
  length: number
}

/** Recorre los últimos N cerrados (con R no nulo) por `closed_at` desc y
 *  cuenta consecutivos del mismo signo. */
export function computeStreak(rows: SetupListRowDTO[]): Streak {
  const closed = rows
    .filter((r) => r.status === "closed" && r.r_multiple !== null)
    .sort(
      (a, b) =>
        Date.parse(b.closed_at ?? "0") - Date.parse(a.closed_at ?? "0"),
    )
  if (closed.length === 0) return { kind: "none", length: 0 }
  const firstR = closed[0]!.r_multiple!
  if (firstR === 0) return { kind: "loss", length: 1 } // empate cuenta como no-win
  const sign: "win" | "loss" = firstR > 0 ? "win" : "loss"
  let length = 0
  for (const r of closed) {
    const v = r.r_multiple!
    const cur: "win" | "loss" = v > 0 ? "win" : "loss"
    if (cur !== sign) break
    length += 1
  }
  return { kind: sign, length }
}

// -----------------------------------------------------------------------------
// Day bucketing
// -----------------------------------------------------------------------------

export interface DayBuckets {
  today: SetupListRowDTO[]
  yesterday: SetupListRowDTO[]
  thisWeek: SetupListRowDTO[]
  thisMonth: SetupListRowDTO[]
  earlier: SetupListRowDTO[]
}

/** Bucketing por día calendario local. Cada setup aterriza en el bucket
 *  correspondiente a su `activityTsMs` (closed_at preferido). Dentro de
 *  cada bucket: orden por activity desc (más reciente arriba). */
export function bucketByDay(rows: SetupListRowDTO[]): DayBuckets {
  const now = Date.now()
  const yesterdayMs = now - DAY_MS
  const buckets: DayBuckets = {
    today: [],
    yesterday: [],
    thisWeek: [],
    thisMonth: [],
    earlier: [],
  }
  for (const r of rows) {
    const ts = activityTsMs(r)
    if (ts === 0) {
      buckets.earlier.push(r)
      continue
    }
    if (isSameLocalDay(ts, now)) {
      buckets.today.push(r)
    } else if (isSameLocalDay(ts, yesterdayMs)) {
      buckets.yesterday.push(r)
    } else if (now - ts < WEEK_MS) {
      buckets.thisWeek.push(r)
    } else if (now - ts < MONTH_MS) {
      buckets.thisMonth.push(r)
    } else {
      buckets.earlier.push(r)
    }
  }
  const byActivityDesc = (a: SetupListRowDTO, b: SetupListRowDTO) =>
    activityTsMs(b) - activityTsMs(a)
  buckets.today.sort(byActivityDesc)
  buckets.yesterday.sort(byActivityDesc)
  buckets.thisWeek.sort(byActivityDesc)
  buckets.thisMonth.sort(byActivityDesc)
  buckets.earlier.sort(byActivityDesc)
  return buckets
}
