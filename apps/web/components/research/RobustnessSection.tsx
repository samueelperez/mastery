"use client"

import { AlertTriangleIcon } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Card, CardContent } from "@/components/ui/card"
import type { StrategyMetricsDTO } from "@/lib/core/api"

interface RobustnessSectionProps {
  metrics: StrategyMetricsDTO
}

/** "¿Es real este edge?" — sección dedicada a las métricas de robustness
 *  de López de Prado, traducidas a copy lego. Sustituye los stat tiles
 *  del MetricsCard viejo. */
export function RobustnessSection({ metrics: m }: RobustnessSectionProps) {
  const dsrTone = toneForDsr(m.deflated_sharpe)
  const psrPct = (m.probabilistic_sharpe * 100).toFixed(0)
  const pboPct =
    m.probability_of_overfit !== null
      ? (m.probability_of_overfit * 100).toFixed(0)
      : null

  return (
    <Card className="border-border bg-card/40">
      <CardContent className="flex flex-col gap-4 p-5">
        <span className="eyebrow">robustez</span>

        {m.overfit_warning && (
          <Alert variant="destructive">
            <AlertTriangleIcon className="size-4" aria-hidden />
            <AlertTitle className="text-[13px] font-semibold">
              No pasa los filtros estándar
            </AlertTitle>
            <AlertDescription className="text-[12px] leading-relaxed">
              DSR &lt; 0.5 o caída máxima &gt; 50%. Tratar como hipótesis,
              no como evidencia.
            </AlertDescription>
          </Alert>
        )}

        <RobMetric
          label="DSR · Sharpe deflacionado"
          value={m.deflated_sharpe.toFixed(2)}
          tone={dsrTone}
          copy="Probabilidad de que el Sharpe sea > 0 tras corregir por las múltiples variantes probadas. ≥ 0.95 = publicable."
        />

        <RobMetric
          label="PSR · Sharpe probabilístico"
          value={`${psrPct}%`}
          tone={
            m.probabilistic_sharpe >= 0.95
              ? "var(--long)"
              : m.probabilistic_sharpe >= 0.5
                ? "var(--amber)"
                : "var(--short)"
          }
          copy="Bajo los retornos observados, probabilidad de que el Sharpe real sea > 0."
        />

        {pboPct !== null && (
          <RobMetric
            label="PBO · overfitting (experimental)"
            value={`${pboPct}%`}
            tone={
              m.probability_of_overfit! < 0.5
                ? "var(--long)"
                : m.probability_of_overfit! < 0.7
                  ? "var(--amber)"
                  : "var(--short)"
            }
            copy="Fracción de folds CPCV donde la mejor variante in-sample resultó mediocre out-of-sample. < 50% indica baja sospecha de overfit."
          />
        )}
      </CardContent>
    </Card>
  )
}

function RobMetric({
  label,
  value,
  tone,
  copy,
}: {
  label: string
  value: string
  tone: string
  copy: string
}) {
  return (
    <div className="grid grid-cols-1 gap-1 border-t border-[color:var(--line-soft)] pt-3 sm:grid-cols-[1fr_auto] sm:items-baseline first:border-t-0 first:pt-0">
      <div className="flex flex-col gap-1">
        <span className="text-[13px] font-semibold text-foreground">
          {label}
        </span>
        <p className="text-[12px] leading-relaxed text-[var(--fg-2)]">
          {copy}
        </p>
      </div>
      <span
        className="font-mono text-2xl font-medium tabular-nums leading-none"
        style={{ color: tone }}
      >
        {value}
      </span>
    </div>
  )
}

function toneForDsr(dsr: number): string {
  if (dsr >= 0.95) return "var(--long)"
  if (dsr >= 0.5) return "var(--amber)"
  return "var(--short)"
}
