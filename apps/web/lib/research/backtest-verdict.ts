/**
 * Verdict compartido entre lista (`BacktestCard`) y detalle (`BacktestVerdict`)
 * de backtests. Mapea las métricas crudas de López de Prado a una etiqueta
 * en castellano + tono visual + 1 frase de explicación.
 *
 * Lógica de los thresholds (Bailey & López de Prado):
 *   - DSR ≥ 0.95 → publicable; el Sharpe deflacionado supera el "esperado bajo
 *     n_trials variantes"; edge sólido.
 *   - DSR ≥ 0.5  → marginal; sigue siendo positivo, pero el filtro estándar
 *     dice "no lo uses como evidencia, sólo como hipótesis".
 *   - DSR < 0.5  → débil; tras corregir por trial-bias, el Sharpe esperado
 *     es indistinguible de cero.
 *   - overfit_warning (DSR < 0.5 OR max_dd > 0.5) → riesgo de overfit; pesa
 *     más que el DSR — incluso un DSR alto con DD>50% es banderaza roja.
 *
 * Si `metrics === null` (run en `running` o `error`), devolvemos `pending`.
 */

import type { StrategyMetricsDTO } from "@/lib/core/api"

export type Verdict = "strong" | "marginal" | "weak" | "overfit" | "pending"

export interface VerdictInfo {
  kind: Verdict
  /** Etiqueta corta para el pill: "edge sólido", "riesgo de overfit". */
  label: string
  /** Una frase plana en castellano, lista para inyectar en el hero. */
  copy: string
  /** Color CSS var del producto (long/amber/short/violet/fg-3). */
  tone: string
  /** Background tinted con la tone, listo para usar en bg de pill. */
  bg: string
  /** Border tinted, listo para `borderColor`. */
  border: string
}

const TONE_LONG = "var(--long)"
const TONE_AMBER = "var(--amber)"
const TONE_SHORT = "var(--short)"
const TONE_FG3 = "var(--fg-3)"
const TONE_VIOLET = "var(--violet)"

function tinted(tone: string, alpha: number): string {
  return `color-mix(in oklch, ${tone} ${alpha}%, transparent)`
}

export function verdictOf(
  metrics: StrategyMetricsDTO | null,
): VerdictInfo {
  if (metrics === null) {
    return {
      kind: "pending",
      label: "ejecutando",
      copy: "Este backtest aún no ha terminado.",
      tone: TONE_VIOLET,
      bg: tinted(TONE_VIOLET, 12),
      border: tinted(TONE_VIOLET, 30),
    }
  }

  const dsr = metrics.deflated_sharpe
  const dd = metrics.max_drawdown

  // overfit_warning sale del backend (DSR < 0.5 OR max_dd > 0.5). Le damos
  // prioridad sobre los thresholds de DSR — un DD descomunal anula el resto.
  if (metrics.overfit_warning) {
    const ddPct = (dd * 100).toFixed(0)
    const dsrStr = dsr.toFixed(2)
    return {
      kind: "overfit",
      label: "riesgo de overfit",
      copy:
        dd > 0.5
          ? `Caída máxima del ${ddPct}%. La muestra del backtest no es suficiente para confiar — trátalo como hipótesis, no como evidencia.`
          : `Sharpe deflacionado ${dsrStr}. Tras corregir por las variantes probadas, el edge probablemente no es real.`,
      tone: TONE_SHORT,
      bg: tinted(TONE_SHORT, 12),
      border: tinted(TONE_SHORT, 30),
    }
  }

  if (dsr >= 0.95) {
    return {
      kind: "strong",
      label: "edge sólido",
      copy: `Sharpe deflacionado de ${dsr.toFixed(2)}. Pasa los filtros de López de Prado: el edge se mantiene tras corregir por las variantes probadas.`,
      tone: TONE_LONG,
      bg: tinted(TONE_LONG, 10),
      border: tinted(TONE_LONG, 30),
    }
  }

  if (dsr >= 0.5) {
    return {
      kind: "marginal",
      label: "edge marginal",
      copy: `DSR de ${dsr.toFixed(2)} — positivo pero al límite. Útil para confirmar hipótesis, no para arriesgar capital.`,
      tone: TONE_AMBER,
      bg: tinted(TONE_AMBER, 10),
      border: tinted(TONE_AMBER, 30),
    }
  }

  return {
    kind: "weak",
    label: "edge débil",
    copy: `DSR ${dsr.toFixed(2)}. El Sharpe esperado tras corregir por trial-bias es indistinguible de cero — la rentabilidad probablemente es ruido.`,
    tone: TONE_FG3,
    bg: tinted(TONE_FG3, 8),
    border: tinted(TONE_FG3, 25),
  }
}
