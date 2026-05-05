/**
 * Helpers para "Estado del edge" en `/research` (Resumen).
 *
 * El backend NO expone un endpoint que nos dé la mejor métrica por
 * strategy_id (la tabla `strategy_metrics` tiene `best_dsr` pero no se
 * surface vía API hoy). Por eso reducimos client-side: agrupamos los
 * backtest_runs por strategy_id, tomamos el de mejor DSR (ties → más
 * reciente), y clasificamos con `verdictOf` el del bucket ganador.
 *
 * Esto es suficiente para el resumen — sin overhead de un nuevo endpoint.
 * Si en el futuro queremos algo más sofisticado (filtros por símbolo o
 * timeframe), promocionar a backend.
 */

import type { BacktestRunSummaryDTO } from "./api"
import { verdictOf } from "./backtest-verdict"

/** Para cada `strategy_id` devuelve su mejor run (mayor DSR; ties → más
 *  reciente por created_at). Solo considera runs con `metrics !== null`
 *  — runs en `running` o `error` no tienen DSR todavía. */
export function bestRunByStrategy(
  runs: BacktestRunSummaryDTO[],
): Map<string, BacktestRunSummaryDTO> {
  const out = new Map<string, BacktestRunSummaryDTO>()
  for (const run of runs) {
    if (!run.metrics) continue
    const cur = out.get(run.strategy_id)
    if (!cur) {
      out.set(run.strategy_id, run)
      continue
    }
    const curDsr = cur.metrics?.deflated_sharpe ?? -Infinity
    const newDsr = run.metrics.deflated_sharpe
    if (newDsr > curDsr) {
      out.set(run.strategy_id, run)
    } else if (newDsr === curDsr) {
      // Tie en DSR: prefiere el más reciente (mejor reflejo del estado actual).
      const curMs = Date.parse(cur.created_at)
      const newMs = Date.parse(run.created_at)
      if (newMs > curMs) out.set(run.strategy_id, run)
    }
  }
  return out
}

export interface EdgeTiers {
  /** strategy_ids que pasan los filtros (DSR ≥ 0.95, sin overfit). */
  strong: string[]
  /** strategy_ids con edge marginal (DSR 0.5–0.95, sin overfit). */
  marginal: string[]
  /** strategy_ids descartados (DSR < 0.5 o overfit_warning). */
  weak: string[]
}

/** Clasifica cada strategy_id por su mejor run usando `verdictOf`. Los
 *  verdict `strong` / `marginal` / `weak` / `overfit` se reducen a tres
 *  buckets (weak + overfit van juntos en `weak`). Los `pending` se
 *  ignoran — un run sin terminar no debe contar. */
export function tierStrategies(runs: BacktestRunSummaryDTO[]): EdgeTiers {
  const best = bestRunByStrategy(runs)
  const tiers: EdgeTiers = { strong: [], marginal: [], weak: [] }
  for (const [strategyId, run] of best.entries()) {
    const v = verdictOf(run.metrics)
    if (v.kind === "strong") tiers.strong.push(strategyId)
    else if (v.kind === "marginal") tiers.marginal.push(strategyId)
    else if (v.kind === "weak" || v.kind === "overfit")
      tiers.weak.push(strategyId)
    // "pending" se ignora — sin DSR aún
  }
  // Orden estable alfabético dentro de cada bucket.
  tiers.strong.sort()
  tiers.marginal.sort()
  tiers.weak.sort()
  return tiers
}
