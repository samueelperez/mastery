"use client"

import { useQuery } from "@tanstack/react-query"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { Spinner } from "@/components/ui/spinner"
import { fetchJournalTrade } from "@/lib/api"
import type { JournalTradeListRowDTO } from "@/lib/api"

interface JournalEntryCardProps {
  trade: JournalTradeListRowDTO
}

export function JournalEntryCard({ trade }: JournalEntryCardProps) {
  const { data, isLoading } = useQuery({
    queryKey: ["journal-trade", trade.id],
    queryFn: ({ signal }) => fetchJournalTrade(trade.id, { signal }),
  })

  return (
    <Card className="border-border bg-card">
      <CardHeader className="space-y-1.5 pb-3">
        <div className="flex items-baseline justify-between gap-3">
          <span className="font-mono text-sm tracking-tight text-foreground">
            {trade.symbol} <span className="text-muted-foreground">{trade.timeframe}</span>
          </span>
          <Badge
            variant="secondary"
            className={
              trade.r_multiple === null
                ? ""
                : trade.r_multiple > 0
                  ? "bg-primary/20 text-primary"
                  : "bg-destructive/20 text-destructive"
            }
          >
            {trade.r_multiple === null
              ? "abierto"
              : `${trade.r_multiple > 0 ? "+" : ""}${trade.r_multiple.toFixed(2)}R`}
          </Badge>
        </div>
        <p className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
          {fmt(trade.trade_ts)} · {trade.side} · {trade.setup_tag}
        </p>
      </CardHeader>
      <CardContent className="space-y-4 pb-6">
        <div className="grid grid-cols-3 gap-3 text-xs">
          <Stat label="entrada" value={`$${trade.entry_px.toFixed(2)}`} />
          <Stat
            label="salida"
            value={trade.exit_px === null ? "—" : `$${trade.exit_px.toFixed(2)}`}
          />
          <Stat label="tamaño" value={trade.size.toString()} />
        </div>

        <div>
          <p className="mb-1.5 font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
            régimen
          </p>
          <p className="font-mono text-xs text-foreground">{trade.regime}</p>
        </div>

        {trade.mistakes && (
          <>
            <Separator />
            <div>
              <p className="mb-1.5 font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
                post-mortem
              </p>
              <p className="text-xs leading-relaxed text-foreground">
                {trade.mistakes}
              </p>
            </div>
          </>
        )}

        {isLoading && (
          <div className="flex items-center gap-2 font-mono text-[11px] text-muted-foreground">
            <Spinner /> cargando detalles…
          </div>
        )}
        {data && (
          <>
            <Separator />
            <div>
              <p className="mb-1.5 font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
                texto embebido · v{data.embedding_version}
              </p>
              <pre className="overflow-x-auto rounded-md bg-background p-2 font-mono text-[11px] text-muted-foreground">
                {data.summary_text}
              </pre>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
        {label}
      </span>
      <span className="font-mono text-sm tabular-nums text-foreground">
        {value}
      </span>
    </div>
  )
}

function fmt(ts: string): string {
  const d = new Date(ts)
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  })
}
