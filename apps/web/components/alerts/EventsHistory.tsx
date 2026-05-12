"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  AlertTriangleIcon,
  CheckIcon,
  CircleIcon,
  Sparkles,
} from "lucide-react"
import { useMemo, useState } from "react"

import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import {
  ToggleGroup,
  ToggleGroupItem,
} from "@/components/ui/toggle-group"
import {
  fetchAlertEvents,
  markEventSeen,
  type AlertEventDTO,
} from "@/lib/core/api"
import { formatTimeAgo } from "@/lib/core/format"
import { cn } from "@/lib/core/utils"

type SeverityFilter = "all" | "high" | "medium" | "low"
type StatusFilter = "all" | "unread"

const SEVERITY_LABEL: Record<SeverityFilter, string> = {
  all: "Todas",
  high: "Alta",
  medium: "Media",
  low: "Baja",
}

const SEVERITY_TONE: Record<"high" | "medium" | "low", string> = {
  high: "var(--short)",
  medium: "var(--amber)",
  low: "var(--fg-3)",
}

/** Histórico de eventos de alerta con filtros (severity / leídos / símbolo).
 *  Backend hoy expone `limit` máx 200 sin offset → fetch single con
 *  limit=200 y filtro client-side. Suficiente para los volúmenes del
 *  producto en F4. Si crece, añadimos `offset` server-side. */
export function EventsHistory() {
  const qc = useQueryClient()
  const [severity, setSeverity] = useState<SeverityFilter>("all")
  const [status, setStatus] = useState<StatusFilter>("all")
  const [symbol, setSymbol] = useState<string>("")

  const { data, isLoading } = useQuery({
    queryKey: ["alert-events", { limit: 200 }],
    queryFn: ({ signal }) => fetchAlertEvents({ limit: 200, signal }),
    refetchInterval: 60_000,
    staleTime: 30_000,
  })

  const events = data ?? []

  const markSeen = useMutation({
    mutationFn: (id: number) => markEventSeen(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alert-events"] }),
  })

  // Símbolos disponibles para el filtro: derivados de los eventos cargados.
  // Sin símbolo en snapshot → fallback a "—" pero se ignora en chips.
  const availableSymbols = useMemo(() => {
    const set = new Set<string>()
    for (const e of events) {
      const sym = (e.snapshot as { symbol?: string }).symbol
      if (sym && typeof sym === "string") set.add(sym)
    }
    return [...set].sort()
  }, [events])

  const visible = useMemo(() => {
    return events.filter((e) => {
      if (severity !== "all" && e.severity !== severity) return false
      if (status === "unread" && e.seen_at !== null) return false
      if (symbol) {
        const sym = (e.snapshot as { symbol?: string }).symbol
        if (sym !== symbol) return false
      }
      return true
    })
  }, [events, severity, status, symbol])

  const filtersActive =
    severity !== "all" || status !== "all" || symbol !== ""

  return (
    <div className="flex flex-col gap-5">
      {/* Filtros */}
      <div className="flex flex-col gap-3 rounded-md border border-border bg-card/30 p-3">
        <FilterRow label="severidad">
          <ToggleGroup
            type="single"
            value={severity}
            onValueChange={(v) => v && setSeverity(v as SeverityFilter)}
            variant="outline"
            size="sm"
            spacing={1}
            className="flex-wrap"
          >
            {(["all", "high", "medium", "low"] as SeverityFilter[]).map((s) => (
              <ToggleGroupItem
                key={s}
                value={s}
                className="text-[12px] font-medium"
              >
                {SEVERITY_LABEL[s]}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </FilterRow>

        <FilterRow label="estado">
          <ToggleGroup
            type="single"
            value={status}
            onValueChange={(v) => v && setStatus(v as StatusFilter)}
            variant="outline"
            size="sm"
            spacing={1}
            className="flex-wrap"
          >
            <ToggleGroupItem value="all" className="text-[12px] font-medium">
              Todos
            </ToggleGroupItem>
            <ToggleGroupItem value="unread" className="text-[12px] font-medium">
              Sin leer
            </ToggleGroupItem>
          </ToggleGroup>
        </FilterRow>

        {availableSymbols.length > 0 && (
          <FilterRow label="símbolo">
            <ToggleGroup
              type="single"
              value={symbol || "__all__"}
              onValueChange={(v) =>
                setSymbol(!v || v === "__all__" ? "" : v)
              }
              variant="outline"
              size="sm"
              spacing={1}
              className="flex-wrap"
            >
              <ToggleGroupItem
                value="__all__"
                className="text-[12px] font-medium"
              >
                Todos
              </ToggleGroupItem>
              {availableSymbols.map((sym) => (
                <ToggleGroupItem
                  key={sym}
                  value={sym}
                  className="font-mono text-[11px] tracking-tight"
                >
                  {sym}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </FilterRow>
        )}

        {filtersActive && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setSeverity("all")
              setStatus("all")
              setSymbol("")
            }}
            className="self-end text-[12px]"
          >
            limpiar filtros
          </Button>
        )}
      </div>

      {/* Timeline */}
      {isLoading ? (
        <p className="text-[13px] text-muted-foreground">cargando eventos…</p>
      ) : visible.length === 0 ? (
        <Card className="border-dashed border-border bg-card/20">
          <CardContent className="flex flex-col items-center gap-1 p-6 text-center">
            <p className="text-[13px] text-foreground">
              {events.length === 0
                ? "Aún no hay eventos."
                : "Sin eventos para este filtro."}
            </p>
            <p className="text-[12px] text-muted-foreground">
              {events.length === 0
                ? "Cuando tus reglas disparen, sus eventos aparecerán aquí."
                : "Ajusta o limpia los filtros para ver más."}
            </p>
          </CardContent>
        </Card>
      ) : (
        <ol className="flex flex-col">
          {visible.map((e, i) => (
            <EventRow
              key={e.id}
              event={e}
              isLast={i === visible.length - 1}
              onMarkSeen={() => markSeen.mutate(e.id)}
              isPending={markSeen.isPending}
            />
          ))}
        </ol>
      )}
    </div>
  )
}

function FilterRow({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="min-w-[78px] font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--fg-3)]">
        {label}
      </span>
      {children}
    </div>
  )
}

interface EventRowProps {
  event: AlertEventDTO
  isLast: boolean
  onMarkSeen: () => void
  isPending: boolean
}

function EventRow({ event, isLast, onMarkSeen, isPending }: EventRowProps) {
  const unread = event.seen_at === null
  const tone = SEVERITY_TONE[event.severity as keyof typeof SEVERITY_TONE]
  const snapshot = event.snapshot as {
    rule_name?: string
    symbol?: string
    timeframe?: string
    matched_conditions?: { left: string; op: string; right: string | number }[]
    values?: Record<string, number>
  }
  const ruleName =
    snapshot.rule_name ??
    (event.kind === "bias_promoted" ? "Sesgo detectado" : "Regla disparada")
  const meta = [snapshot.symbol, snapshot.timeframe].filter(Boolean).join(" · ")

  // Top 3 valores numéricos del snapshot — útil para entender contexto
  // sin abrir el panel completo.
  const valuePreview = useMemo(() => {
    if (!snapshot.values) return ""
    const entries = Object.entries(snapshot.values)
      .filter(([, v]) => typeof v === "number")
      .slice(0, 3)
    return entries
      .map(([k, v]) => `${k} = ${formatNum(v as number)}`)
      .join(" · ")
  }, [snapshot.values])

  const Icon =
    event.kind === "bias_promoted"
      ? Sparkles
      : event.severity === "high"
        ? AlertTriangleIcon
        : CircleIcon

  return (
    <li className="relative grid grid-cols-[20px_1fr] gap-3 pb-4">
      <div className="flex flex-col items-center">
        <span
          aria-hidden
          className="grid size-5 place-items-center rounded-full"
          style={{
            backgroundColor: `color-mix(in oklch, ${tone} 15%, transparent)`,
            border: `1px solid color-mix(in oklch, ${tone} 35%, transparent)`,
          }}
        >
          <Icon className="size-3" style={{ color: tone }} aria-hidden />
        </span>
        {!isLast && (
          <span
            aria-hidden
            className="mt-1 w-px flex-1"
            style={{
              backgroundColor: `color-mix(in oklch, ${tone} 20%, transparent)`,
            }}
          />
        )}
      </div>
      <div
        className={cn(
          "flex flex-col gap-1.5 rounded-md border border-border/40 px-3 py-2",
          unread ? "bg-card/60" : "bg-transparent",
        )}
      >
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-[14px] font-medium text-foreground">
            {ruleName}
          </span>
          <span className="font-mono text-[10px] tabular-nums text-[var(--fg-3)]">
            {formatTimeAgo(event.fired_at)}
          </span>
        </div>
        {meta && (
          <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
            {meta} · severidad {event.severity}
          </span>
        )}
        {valuePreview && (
          <p className="font-mono text-[11px] tabular-nums text-[var(--fg-2)]">
            {valuePreview}
          </p>
        )}
        {unread && (
          <button
            type="button"
            onClick={onMarkSeen}
            disabled={isPending}
            className="mt-1 inline-flex w-fit items-center gap-1 font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--fg-3)] transition-colors hover:text-foreground"
          >
            <CheckIcon className="size-3" aria-hidden />
            marcar leído
          </button>
        )}
      </div>
    </li>
  )
}

function formatNum(n: number): string {
  if (Number.isInteger(n)) return String(n)
  if (Math.abs(n) >= 1000) return n.toFixed(1)
  return n.toLocaleString(undefined, { maximumFractionDigits: 4 })
}
