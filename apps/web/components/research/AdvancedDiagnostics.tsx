"use client"

import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import type { BacktestRunDetailDTO, StrategyMetricsDTO } from "@/lib/api"

interface AdvancedDiagnosticsProps {
  run: BacktestRunDetailDTO
}

interface Diagnostic {
  label: string
  value: string
  copy: string
}

/** Accordion colapsado por defecto con métricas avanzadas + 1-línea de
 *  explicación cada una. El usuario casual no las necesita; el power-user
 *  las despliega. */
export function AdvancedDiagnostics({ run }: AdvancedDiagnosticsProps) {
  const m = run.metrics
  if (!m) return null

  const diagnostics = buildDiagnostics(m, run.timeframe)

  return (
    <Accordion type="single" collapsible className="w-full">
      <AccordionItem
        value="advanced"
        className="rounded-md border border-border bg-card/30 px-4"
      >
        <AccordionTrigger className="font-mono text-[11px] uppercase tracking-[0.16em] text-[var(--fg-2)] hover:no-underline">
          diagnóstico avanzado
        </AccordionTrigger>
        <AccordionContent>
          <dl className="flex flex-col divide-y divide-[color:var(--line-soft)]">
            {diagnostics.map((d) => (
              <div
                key={d.label}
                className="grid grid-cols-1 gap-1 py-2 sm:grid-cols-[160px_1fr_auto] sm:items-baseline sm:gap-3"
              >
                <dt className="text-[13px] font-semibold text-foreground">
                  {d.label}
                </dt>
                <p className="text-[12px] leading-relaxed text-[var(--fg-2)] sm:order-3 sm:text-right">
                  {d.copy}
                </p>
                <dd className="font-mono text-[13px] tabular-nums text-foreground sm:order-2">
                  {d.value}
                </dd>
              </div>
            ))}
          </dl>
        </AccordionContent>
      </AccordionItem>
    </Accordion>
  )
}

function buildDiagnostics(
  m: StrategyMetricsDTO,
  timeframe: string,
): Diagnostic[] {
  const ddBars = m.max_drawdown_duration_bars
  const ddDays = ddBars > 0 ? barsToDays(ddBars, timeframe) : 0

  return [
    {
      label: "Sortino",
      value: m.sortino === null ? "—" : m.sortino.toFixed(2),
      copy:
        m.sortino === null
          ? "Indefinido — no hay returns negativos en la muestra."
          : "Sharpe penalizando solo la volatilidad bajista (los upside spikes no cuentan).",
    },
    {
      label: "Calmar",
      value: m.calmar.toFixed(2),
      copy:
        "Retorno anualizado (CAGR) dividido por max drawdown — cuántas veces 'vale la pena' el peor pinchazo.",
    },
    {
      label: "Ulcer Index",
      value: m.ulcer_index.toFixed(2),
      copy:
        "Índice de 'dolor' — penaliza tanto la profundidad como la duración del drawdown. Más bajo = más cómodo.",
    },
    {
      label: "Tail Ratio",
      value: m.tail_ratio.toFixed(2),
      copy:
        "Ratio entre cola derecha (P95) y cola izquierda (P5) de los retornos. > 1 indica que los grandes ganadores dominan a los grandes perdedores.",
    },
    {
      label: "Skew",
      value: m.skew.toFixed(2),
      copy:
        "Asimetría de los retornos. Negativo = los días malos son más raros pero más brutales; positivo = al revés.",
    },
    {
      label: "Excess Kurtosis",
      value: m.kurtosis.toFixed(2),
      copy:
        "Cola pesada — alto = los movimientos extremos son frecuentes (fat tails); 0 = distribución normal.",
    },
    {
      label: "Win rate",
      value: `${(m.win_rate * 100).toFixed(0)}%`,
      copy: "Porcentaje de trades en positivo.",
    },
    {
      label: "Avg win",
      value: `${m.avg_win_R >= 0 ? "+" : ""}${m.avg_win_R.toFixed(2)}R`,
      copy: "R-multiple promedio de los trades ganadores.",
    },
    {
      label: "Avg loss",
      value: `${m.avg_loss_R.toFixed(2)}R`,
      copy: "R-multiple promedio de los trades perdedores.",
    },
    {
      label: "Max DD duration",
      value:
        ddBars > 0
          ? `${ddBars} bars · ${ddDays} día${ddDays === 1 ? "" : "s"}`
          : "—",
      copy:
        "Cuánto tardó la equity en recuperarse del peor punto. Larga = test de paciencia; corta = drawdowns rápidos.",
    },
  ]
}

function barsToDays(bars: number, timeframe: string): number {
  const minPerBar = (
    {
      "1m": 1,
      "5m": 5,
      "15m": 15,
      "30m": 30,
      "1h": 60,
      "2h": 120,
      "4h": 240,
      "1d": 1440,
    } as Record<string, number>
  )[timeframe]
  if (!minPerBar) return Math.round(bars / 24)
  return Math.max(1, Math.round((bars * minPerBar) / 1440))
}
