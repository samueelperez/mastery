"use client"

import { useState } from "react"

import { Card } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/core/utils"
import type { JournalTradeListRowDTO } from "@/lib/core/api"

import { JournalEntryCard } from "./JournalEntryCard"

interface JournalListProps {
  trades: JournalTradeListRowDTO[]
  loading: boolean
  error?: string
}

export function JournalList({ trades, loading, error }: JournalListProps) {
  const [selected, setSelected] = useState<JournalTradeListRowDTO | null>(null)

  if (loading && trades.length === 0) {
    return (
      <div className="flex flex-col gap-2">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
    )
  }
  if (error) {
    return <p className="text-xs text-destructive">{error}</p>
  }
  if (trades.length === 0) {
    return (
      <Card className="border-dashed border-border bg-card/20 p-6 text-center">
        <p className="font-mono text-xs uppercase tracking-widest text-muted-foreground">
          aún no hay trades registrados
        </p>
        <p className="mt-2 text-xs text-muted-foreground">
          Importa con <span className="font-mono text-foreground">scripts/import_journal.py</span> o
          deja que el copiloto registre cuando cierres uno.
        </p>
      </Card>
    )
  }

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,420px)]">
      <div className="overflow-x-auto rounded-md border border-border">
        <table className="w-full min-w-[34rem] text-xs">
          <thead className="bg-[oklch(0.18_0.018_260)]">
            <tr>
              <Th>fecha</Th>
              <Th>símbolo</Th>
              <Th>lado</Th>
              <Th align="right">R</Th>
              <Th>setup</Th>
              <Th>régimen</Th>
              <Th>modo</Th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[color:var(--line-soft)]">
            {trades.map((t) => (
              <tr
                key={t.id}
                className={cn(
                  "relative transition-colors duration-150 ease-out hover:bg-[var(--bg-2)]",
                  "focus-within:bg-[var(--violet-soft)]",
                  selected?.id === t.id &&
                    "bg-[var(--violet-soft)] shadow-[inset_0_0_0_1px_oklch(0.55_0.16_290_/_0.4)]",
                )}
              >
                {/* Stretched-button pattern: keeps native <table>/<tr>/<td>
                    semantics for screen readers; clicking anywhere selects. */}
                <Td mono>
                  <button
                    type="button"
                    onClick={() => setSelected(t)}
                    aria-pressed={selected?.id === t.id}
                    aria-label={`seleccionar ${t.symbol} ${t.timeframe} ${t.side} del ${shortDate(t.trade_ts)}`}
                    className="font-mono after:absolute after:inset-0 after:content-[''] focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2"
                  >
                    {shortDate(t.trade_ts)}
                  </button>
                </Td>
                <Td>
                  {t.symbol}
                  <span className="ml-1 text-muted-foreground">{t.timeframe}</span>
                </Td>
                <Td>
                  <span
                    className={cn(
                      "pill-status",
                      t.side === "long" ? "pill-status-ok" : "pill-status-err",
                    )}
                  >
                    {t.side}
                  </span>
                </Td>
                <Td
                  align="right"
                  mono
                  className={
                    t.r_multiple === null
                      ? "text-[var(--fg-3)]"
                      : t.r_multiple > 0
                        ? "text-[var(--long)]"
                        : "text-[var(--short)]"
                  }
                >
                  {t.r_multiple === null
                    ? "abierto"
                    : `${t.r_multiple > 0 ? "+" : ""}${t.r_multiple.toFixed(2)}R`}
                </Td>
                <Td>{t.setup_tag}</Td>
                <Td className="text-[var(--fg-2)]">{t.regime}</Td>
                <Td className="text-[var(--fg-3)] text-[10px] uppercase tracking-[0.08em]">
                  {t.mode.replace("_", " ")}
                </Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <aside className="lg:sticky lg:top-6">
        {selected ? (
          <JournalEntryCard trade={selected} />
        ) : (
          <Card className="border-dashed border-border bg-card/20 p-6 text-center">
            <p className="font-mono text-xs uppercase tracking-widest text-muted-foreground">
              haz click en un trade para inspeccionarlo
            </p>
          </Card>
        )}
      </aside>
    </div>
  )
}

function Th({
  children,
  align = "left",
}: {
  children: React.ReactNode
  align?: "left" | "right"
}) {
  return (
    <th
      className={cn(
        "px-3 py-2 pointer-coarse:py-3 font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--fg-3)] font-medium",
        align === "right" ? "text-right" : "text-left",
      )}
    >
      {children}
    </th>
  )
}

function Td({
  children,
  align = "left",
  mono,
  className,
}: {
  children: React.ReactNode
  align?: "left" | "right"
  mono?: boolean
  className?: string
}) {
  return (
    <td
      className={cn(
        "px-3 py-2 pointer-coarse:py-4",
        align === "right" ? "text-right" : "text-left",
        mono && "font-mono tabular-nums",
        className,
      )}
    >
      {children}
    </td>
  )
}

function shortDate(ts: string): string {
  const d = new Date(ts)
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "2-digit",
  })
}
