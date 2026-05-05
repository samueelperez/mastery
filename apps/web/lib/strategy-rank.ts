/**
 * Ranking de estrategias para el hero "mejor estrategia" de
 * `/research/strategies`.
 *
 * Score: `avg_r × √min(n_closed, 30)`.
 *
 * El factor `√min(n,30)` resuelve el problema clásico de "una strategy con
 * 2 cierres a +1.5R parece mejor que otra con 30 cierres a +0.5R". El cap a
 * 30 evita que muestras enormes dominen indefinidamente — a partir de N=30
 * el premio por más muestra se aplana.
 *
 * Filtrado: el usuario eligió `minN=5` para que el hero no premie suerte
 * pura. Strategies con menos de 5 cierres no compiten.
 */

import type { StrategyWinrateDTO } from "./api"

const N_CAP = 30
const DEFAULT_MIN_N = 5

export interface RankedStrategy extends StrategyWinrateDTO {
  /** Score compuesto que pondera avg_r por √min(n,30). */
  score: number
}

function scoreOf(row: StrategyWinrateDTO): number {
  const avg = row.avg_r ?? 0
  const cappedN = Math.min(row.n_closed, N_CAP)
  return avg * Math.sqrt(cappedN)
}

/** Devuelve las strategies que pasan `minN`, decoradas con `score` y
 *  ordenadas desc. Ties: avg_r más alto, luego n_closed más alto. */
export function rankStrategies(
  rows: StrategyWinrateDTO[],
  minN: number = DEFAULT_MIN_N,
): RankedStrategy[] {
  return rows
    .filter((r) => r.n_closed >= minN && r.avg_r !== null)
    .map((r) => ({ ...r, score: scoreOf(r) }))
    .sort((a, b) => {
      if (b.score !== a.score) return b.score - a.score
      const aAvg = a.avg_r ?? 0
      const bAvg = b.avg_r ?? 0
      if (bAvg !== aAvg) return bAvg - aAvg
      return b.n_closed - a.n_closed
    })
}

/** Top-1 strategy o null si nadie supera `minN`. */
export function pickBestStrategy(
  rows: StrategyWinrateDTO[],
  minN: number = DEFAULT_MIN_N,
): RankedStrategy | null {
  const ranked = rankStrategies(rows, minN)
  return ranked[0] ?? null
}
