"use client"

import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Card } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"
import type { JournalTradeListRowDTO } from "@/lib/api"

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
          no trades logged yet
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
      <div className="overflow-hidden rounded-md border border-border">
        <table className="w-full text-xs">
          <thead className="bg-card text-[11px] uppercase tracking-widest text-muted-foreground">
            <tr>
              <Th>date</Th>
              <Th>symbol</Th>
              <Th>side</Th>
              <Th align="right">R</Th>
              <Th>setup</Th>
              <Th>regime</Th>
              <Th>mode</Th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/30">
            {trades.map((t) => (
              <tr
                key={t.id}
                tabIndex={0}
                role="button"
                aria-pressed={selected?.id === t.id}
                aria-label={`select ${t.symbol} ${t.timeframe} ${t.side} from ${shortDate(t.trade_ts)}`}
                onClick={() => setSelected(t)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault()
                    setSelected(t)
                  }
                }}
                className={cn(
                  "cursor-pointer transition-colors duration-150 ease-out hover:bg-accent/10",
                  "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:-outline-offset-2",
                  selected?.id === t.id && "bg-accent/20",
                )}
              >
                <Td mono>{shortDate(t.trade_ts)}</Td>
                <Td>
                  {t.symbol}
                  <span className="ml-1 text-muted-foreground">{t.timeframe}</span>
                </Td>
                <Td>
                  <Badge
                    variant={t.side === "long" ? "default" : "secondary"}
                    className={
                      t.side === "long"
                        ? "bg-primary/20 text-primary hover:bg-primary/30"
                        : ""
                    }
                  >
                    {t.side}
                  </Badge>
                </Td>
                <Td
                  align="right"
                  mono
                  className={
                    t.r_multiple === null
                      ? "text-muted-foreground"
                      : t.r_multiple > 0
                        ? "text-primary"
                        : "text-destructive"
                  }
                >
                  {t.r_multiple === null
                    ? "open"
                    : `${t.r_multiple > 0 ? "+" : ""}${t.r_multiple.toFixed(2)}R`}
                </Td>
                <Td>{t.setup_tag}</Td>
                <Td className="text-muted-foreground">{t.regime}</Td>
                <Td className="text-muted-foreground text-[11px] uppercase">
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
              click a trade to inspect
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
      className={`px-3 py-2 pointer-coarse:py-4 font-medium ${align === "right" ? "text-right" : "text-left"}`}
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
