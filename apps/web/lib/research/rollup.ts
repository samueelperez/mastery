/**
 * KPI agregado del Resumen de /research.
 *
 * Calcula a partir de los setups cerrados (status='closed', cualquier source):
 * - verdict: empty | learning | good | review.
 * - copy en castellano plano (sin DSR ni jerga) explicando el verdict.
 * - lastActivityMs: timestamp del cierre más reciente.
 * - totalClosed, avgR: aggregates simples.
 *
 * Sin endpoint backend dedicado — fetcheamos el feed de cerrados y
 * reducimos client-side. Coherente con `/journal/page.tsx` que ya hace
 * algo similar para week stats.
 */

import type { SetupListRowDTO } from "@/lib/core/api"

const LEARNING_THRESHOLD = 10

export type OverviewVerdict = "empty" | "learning" | "good" | "review"

export interface OverviewKpi {
  verdict: OverviewVerdict
  /** Etiqueta corta del verdict, lista para usar en sans semibold 32px. */
  verdictLabel: string
  /** 1-2 frases plain-language que explican el estado. */
  copy: string
  /** Timestamp ms del cierre más reciente; null si 0 cerrados. */
  lastActivityMs: number | null
  totalClosed: number
  /** Promedio de r_multiple sobre cerrados con r_multiple no nulo. */
  avgR: number | null
}

/** Bucket de verdict basado en muestra y signo de avg_r:
 *
 *   0 cerrados                  → empty
 *   1..9 cerrados               → learning  (sin juicio)
 *   ≥10 cerrados, avg_r > 0     → good
 *   ≥10 cerrados, avg_r ≤ 0     → review
 *
 * El umbral de 10 es deliberadamente bajo: queremos felicitar al user en
 * cuanto haya señal, pero la barra para "vas bien" requiere al menos diez
 * cierres para que no sea suerte. */
export function computeOverviewKpi(closed: SetupListRowDTO[]): OverviewKpi {
  let totalClosed = 0
  let rSum = 0
  let rCount = 0
  let lastMs = 0
  for (const r of closed) {
    if (r.status !== "closed") continue
    totalClosed += 1
    if (r.r_multiple !== null) {
      rSum += r.r_multiple
      rCount += 1
    }
    const closedMs = r.closed_at ? Date.parse(r.closed_at) : 0
    if (Number.isFinite(closedMs) && closedMs > lastMs) {
      lastMs = closedMs
    }
  }
  const avgR = rCount > 0 ? rSum / rCount : null
  const lastActivityMs = lastMs > 0 ? lastMs : null

  if (totalClosed === 0) {
    return {
      verdict: "empty",
      verdictLabel: "Sin datos aún",
      copy: "Cuando cierres tu primer trade, este resumen empieza a contar tu historia. Pídele al copiloto un trade idea o importa un CSV para empezar.",
      lastActivityMs: null,
      totalClosed: 0,
      avgR: null,
    }
  }

  if (totalClosed < LEARNING_THRESHOLD) {
    return {
      verdict: "learning",
      verdictLabel: "Aprendiendo",
      copy: buildPartialCopy(totalClosed, avgR),
      lastActivityMs,
      totalClosed,
      avgR,
    }
  }

  // ≥ 10 cerrados — emitimos juicio.
  if (avgR !== null && avgR > 0) {
    return {
      verdict: "good",
      verdictLabel: "Vas bien",
      copy: buildGoodCopy(totalClosed, avgR),
      lastActivityMs,
      totalClosed,
      avgR,
    }
  }

  return {
    verdict: "review",
    verdictLabel: "A revisar",
    copy: buildReviewCopy(totalClosed, avgR),
    lastActivityMs,
    totalClosed,
    avgR,
  }
}

function buildPartialCopy(n: number, avgR: number | null): string {
  const trades = n === 1 ? "1 trade cerrado" : `${n} trades cerrados`
  if (avgR === null) {
    return `Llevas ${trades}. Aún sin r-multiple suficiente para juzgar.`
  }
  const sign = avgR >= 0 ? "+" : ""
  return `Llevas ${trades}, promediando ${sign}${avgR.toFixed(2)}R por trade. Necesitas al menos 10 cierres para que el promedio sea fiable.`
}

function buildGoodCopy(n: number, avgR: number): string {
  const sign = avgR >= 0 ? "+" : ""
  return `Llevas ${n} trades cerrados, ganando ${sign}${avgR.toFixed(2)}R por trade en promedio.`
}

function buildReviewCopy(n: number, avgR: number | null): string {
  if (avgR === null) {
    return `Llevas ${n} trades cerrados pero sin r-multiple registrado. Revisa cómo estás capturando los cierres.`
  }
  const sign = avgR >= 0 ? "+" : ""
  return `Llevas ${n} trades cerrados con un promedio de ${sign}${avgR.toFixed(2)}R por trade. Toca pararse a entender qué no está funcionando.`
}
