"use client"

import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import type {
  BacktestRunDetailDTO,
  StrategyRegistryDTO,
} from "@/lib/core/api"

interface StrategyExplainerProps {
  run: BacktestRunDetailDTO
  registryEntry?: StrategyRegistryDTO
}

/** Sección "qué hace esta estrategia". Description del registry +
 *  parámetros usados + universo + costes. Si el registryEntry no llegó
 *  todavía (loading) o no existe (estrategia removida del registry tras
 *  un deploy), se muestra fallback minimalista. */
export function StrategyExplainer({
  run,
  registryEntry,
}: StrategyExplainerProps) {
  const displayName = registryEntry?.name ?? run.strategy_id
  const description =
    registryEntry?.description ??
    "Estrategia personalizada o removida del registry — no hay descripción registrada."

  const paramEntries = Object.entries(run.params).sort(([a], [b]) =>
    a.localeCompare(b),
  )

  return (
    <Card className="border-border bg-card/40">
      <CardHeader className="pb-2">
        <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--fg-3)]">
          estrategia
        </span>
        <CardTitle className="text-lg font-semibold tracking-tight text-foreground">
          {displayName}
        </CardTitle>
        {registryEntry && (
          <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
            {run.strategy_id}
          </span>
        )}
        <p className="text-[13px] leading-relaxed text-[var(--fg-2)]">
          {description}
        </p>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-6 pt-4 sm:grid-cols-2">
        <Block title="parámetros usados">
          {paramEntries.length === 0 ? (
            <p className="font-mono text-[11px] text-[var(--fg-3)]">
              sin parámetros — defaults del registry.
            </p>
          ) : (
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 font-mono text-[11px] tabular-nums">
              {paramEntries.map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-[var(--fg-3)]">{k}</dt>
                  <dd className="text-foreground">{formatParamValue(v)}</dd>
                </div>
              ))}
            </dl>
          )}
        </Block>
        <Block title="universo y costes">
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 font-mono text-[11px] tabular-nums">
            <div className="contents">
              <dt className="text-[var(--fg-3)]">símbolo</dt>
              <dd className="text-foreground">
                {run.symbol} · {run.timeframe}
              </dd>
            </div>
            <div className="contents">
              <dt className="text-[var(--fg-3)]">rango</dt>
              <dd className="text-foreground">
                {formatDate(run.range_start)} → {formatDate(run.range_end)}
              </dd>
            </div>
            <div className="contents">
              <dt className="text-[var(--fg-3)]">fees</dt>
              <dd className="text-foreground">{run.fees_bps} bps</dd>
            </div>
            <div className="contents">
              <dt className="text-[var(--fg-3)]">slippage</dt>
              <dd className="text-foreground">{run.slippage_atr}× ATR</dd>
            </div>
          </dl>
        </Block>
      </CardContent>
    </Card>
  )
}

function Block({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-2">
      <span className="eyebrow">{title}</span>
      {children}
    </div>
  )
}

function formatParamValue(v: unknown): string {
  if (typeof v === "number") return String(v)
  if (typeof v === "string") return v
  if (typeof v === "boolean") return v ? "true" : "false"
  if (v === null) return "null"
  return JSON.stringify(v)
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
  })
}
