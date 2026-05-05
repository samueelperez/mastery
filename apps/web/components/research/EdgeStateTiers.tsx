"use client"

import { ArrowRightIcon } from "lucide-react"
import Link from "next/link"

import { Card, CardContent } from "@/components/ui/card"
import type { EdgeTiers } from "@/lib/edge-state"

interface EdgeStateTiersProps {
  tiers: EdgeTiers
  /** Total de strategies clasificadas (para detectar empty global). */
  totalClassified: number
}

interface TierDef {
  key: keyof EdgeTiers
  label: string
  copy: string
  tone: string
  bg: string
  border: string
}

function tinted(tone: string, alpha: number): string {
  return `color-mix(in oklch, ${tone} ${alpha}%, transparent)`
}

const TIERS: TierDef[] = [
  {
    key: "strong",
    label: "Funcionan bien",
    copy: "Sharpe sólido, sin sospecha de overfitting.",
    tone: "var(--long)",
    bg: tinted("var(--long)", 8),
    border: tinted("var(--long)", 30),
  },
  {
    key: "marginal",
    label: "En duda",
    copy: "Sharpe positivo pero al límite — monitorear.",
    tone: "var(--amber)",
    bg: tinted("var(--amber)", 8),
    border: tinted("var(--amber)", 30),
  },
  {
    key: "weak",
    label: "No funcionan",
    copy: "Probable overfitting o caída excesiva.",
    tone: "var(--short)",
    bg: tinted("var(--short)", 8),
    border: tinted("var(--short)", 30),
  },
]

export function EdgeStateTiers({
  tiers,
  totalClassified,
}: EdgeStateTiersProps) {
  if (totalClassified === 0) {
    return (
      <section className="flex flex-col gap-3">
        <span className="eyebrow">estado del edge</span>
        <Card className="border-dashed border-border bg-card/20">
          <CardContent className="flex flex-col items-center gap-1 p-6 text-center">
            <p className="text-[14px] text-foreground">
              Aún no has ejecutado ningún backtest.
            </p>
            <p className="text-[13px] text-muted-foreground">
              Pídele al copiloto un backtest de tu estrategia favorita y los
              resultados aparecen aquí clasificados.
            </p>
          </CardContent>
        </Card>
      </section>
    )
  }

  return (
    <section className="flex flex-col gap-3">
      <span className="eyebrow">estado del edge</span>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {TIERS.map((t) => {
          const count = tiers[t.key].length
          return (
            <Link
              key={t.key}
              href="/research/backtests"
              className="rounded-md outline-none transition-colors focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2"
              aria-label={`${count} estrategias ${t.label.toLowerCase()} — ver backtests`}
            >
              <Card
                className="h-full transition-colors hover:bg-card/80"
                style={{
                  backgroundColor: t.bg,
                  borderColor: t.border,
                }}
              >
                <CardContent className="flex h-full flex-col gap-3 p-4">
                  <div className="flex items-center justify-between">
                    <span
                      className="font-mono text-[10px] uppercase tracking-[0.14em]"
                      style={{ color: t.tone }}
                    >
                      {t.label}
                    </span>
                    <ArrowRightIcon
                      className="size-3.5 text-[var(--fg-3)] transition-transform group-hover:translate-x-0.5"
                      aria-hidden
                    />
                  </div>
                  <span
                    className="font-mono text-4xl font-medium tabular-nums leading-none"
                    style={{ color: t.tone }}
                  >
                    {count}
                  </span>
                  <p className="text-[12px] leading-relaxed text-[var(--fg-2)]">
                    {t.copy}
                  </p>
                </CardContent>
              </Card>
            </Link>
          )
        })}
      </div>
    </section>
  )
}
