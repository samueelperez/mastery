"use client"

import { Button } from "@/components/ui/button"
import {
  ToggleGroup,
  ToggleGroupItem,
} from "@/components/ui/toggle-group"
import type { SetupStatus, SetupStatusCountsDTO } from "@/lib/core/api"
import { WATCH_SYMBOLS } from "@/lib/store/active-symbol"

export type SourceFilter = "all" | "agent"
export type StatusFilter = "all" | SetupStatus

interface DiarioFiltersProps {
  source: SourceFilter
  setSource: (v: SourceFilter) => void
  status: StatusFilter
  setStatus: (v: StatusFilter) => void
  symbol: string  // "" = todos, o un símbolo concreto
  setSymbol: (v: string) => void
  /** Counts por status del fetch actual; permite mostrar números inline. */
  counts?: SetupStatusCountsDTO
  onClear?: () => void
  showClear?: boolean
}

const STATUS_LABEL: Record<StatusFilter, string> = {
  all: "Todos",
  pending: "Esperando",
  active: "Activos",
  closed: "Cerrados",
  cancelled: "Cancelados",
}

const STATUS_KEYS: StatusFilter[] = [
  "all",
  "pending",
  "active",
  "closed",
  "cancelled",
]

export function DiarioFilters({
  source,
  setSource,
  status,
  setStatus,
  symbol,
  setSymbol,
  counts,
  onClear,
  showClear,
}: DiarioFiltersProps) {
  return (
    <div className="flex flex-col gap-3 rounded-md border border-border bg-card/30 p-3">
      <FilterRow label="fuente">
        <ToggleGroup
          type="single"
          value={source}
          onValueChange={(v) => v && setSource(v as SourceFilter)}
          variant="outline"
          size="sm"
          spacing={1}
          className="flex-wrap"
        >
          <ToggleGroupItem value="agent" className="text-[12px] font-medium">
            Agente
          </ToggleGroupItem>
          <ToggleGroupItem value="all" className="text-[12px] font-medium">
            Todos
          </ToggleGroupItem>
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
          {STATUS_KEYS.map((s) => {
            const count =
              s === "all"
                ? counts
                  ? counts.pending +
                    counts.active +
                    counts.closed +
                    counts.cancelled
                  : null
                : counts?.[s] ?? null
            return (
              <ToggleGroupItem
                key={s}
                value={s}
                className="text-[12px] font-medium"
              >
                {STATUS_LABEL[s]}
                {count !== null && (
                  <span className="ml-1.5 font-mono text-[10px] tabular-nums text-[var(--fg-3)]">
                    {count}
                  </span>
                )}
              </ToggleGroupItem>
            )
          })}
        </ToggleGroup>
      </FilterRow>

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
          <ToggleGroupItem value="__all__" className="text-[12px] font-medium">
            Todos
          </ToggleGroupItem>
          {WATCH_SYMBOLS.map((sym) => (
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

      {showClear && onClear && (
        <Button
          variant="ghost"
          size="sm"
          onClick={onClear}
          className="self-end text-[12px]"
        >
          limpiar filtros
        </Button>
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
