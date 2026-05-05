"use client"

import { useInfiniteQuery, useQuery } from "@tanstack/react-query"
import { useMemo, useState } from "react"

import { BacktestCard } from "@/components/research/BacktestCard"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import {
  ToggleGroup,
  ToggleGroupItem,
} from "@/components/ui/toggle-group"
import {
  fetchBacktests,
  fetchStrategyRegistry,
  type BacktestRunSummaryDTO,
  type StrategyRegistryDTO,
} from "@/lib/api"
import { WATCH_SYMBOLS } from "@/lib/store/active-symbol"

const TIMEFRAMES = ["15m", "1h", "4h", "1d"] as const
const PAGE_SIZE = 50
const ANY = "__any__"

export default function BacktestsListPage() {
  const [strategy, setStrategy] = useState<string>(ANY)
  const [symbol, setSymbol] = useState<string>(ANY)
  const [timeframe, setTimeframe] = useState<string>(ANY)

  const filters = {
    strategy_id: strategy === ANY ? undefined : strategy,
    symbol: symbol === ANY ? undefined : symbol,
    timeframe: timeframe === ANY ? undefined : timeframe,
  }

  const query = useInfiniteQuery({
    queryKey: ["backtests-paged", filters],
    queryFn: ({ pageParam, signal }) =>
      fetchBacktests({
        ...filters,
        limit: PAGE_SIZE,
        offset: pageParam,
        signal,
      }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) => {
      if (lastPage.length < PAGE_SIZE) return undefined
      return allPages.length * PAGE_SIZE
    },
  })

  // Strategy registry — descripciones legibles. Cacheado agresivo (cambia
  // sólo con un deploy; el endpoint marca Cache-Control 5min).
  const registryQuery = useQuery({
    queryKey: ["strategy-registry"],
    queryFn: ({ signal }) => fetchStrategyRegistry({ signal }),
    staleTime: Infinity,
  })

  const registryById = useMemo(() => {
    const out: Record<string, StrategyRegistryDTO> = {}
    for (const s of registryQuery.data ?? []) out[s.id] = s
    return out
  }, [registryQuery.data])

  const rows: BacktestRunSummaryDTO[] = useMemo(
    () => query.data?.pages.flatMap((p) => p) ?? [],
    [query.data],
  )

  const strategiesAvailable = useMemo(() => {
    const set = new Set<string>()
    for (const s of registryQuery.data ?? []) set.add(s.id)
    for (const r of rows) set.add(r.strategy_id)
    return [...set].sort()
  }, [registryQuery.data, rows])

  const filtersActive =
    strategy !== ANY || symbol !== ANY || timeframe !== ANY
  const clearFilters = () => {
    setStrategy(ANY)
    setSymbol(ANY)
    setTimeframe(ANY)
  }

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h2 className="text-xl font-semibold tracking-tight text-foreground">
          Backtests
        </h2>
        <p className="text-[13px] text-muted-foreground">
          {rows.length} run{rows.length === 1 ? "" : "s"} · ordenados por
          fecha de ejecución descendente · click para ver narrativa completa.
        </p>
      </header>

      <FiltersToolbar
        strategy={strategy}
        setStrategy={setStrategy}
        strategiesAvailable={strategiesAvailable}
        strategyLabelOf={(id) => registryById[id]?.name ?? id}
        symbol={symbol}
        setSymbol={setSymbol}
        timeframe={timeframe}
        setTimeframe={setTimeframe}
        showClear={filtersActive}
        onClear={clearFilters}
      />

      {query.isLoading && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-44 w-full" />
          ))}
        </div>
      )}

      {query.error && (
        <p className="font-mono text-xs text-destructive">
          Error: {(query.error as Error).message}
        </p>
      )}

      {!query.isLoading && rows.length === 0 && (
        <div className="rounded-md border border-dashed border-border bg-card/20 p-6 text-center">
          <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-[var(--fg-3)]">
            sin backtests
          </p>
          <p className="mt-1 text-[13px] text-muted-foreground">
            Pídele al copiloto que ejecute uno y aparecerá aquí en cuanto
            termine.
          </p>
        </div>
      )}

      {rows.length > 0 && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {rows.map((r) => (
            <BacktestCard key={r.id} run={r} registry={registryById} />
          ))}
        </div>
      )}

      {query.hasNextPage && (
        <div className="flex justify-center">
          <Button
            variant="outline"
            size="sm"
            onClick={() => query.fetchNextPage()}
            disabled={query.isFetchingNextPage}
            className="font-mono text-[11px] uppercase tracking-[0.12em]"
          >
            {query.isFetchingNextPage ? "cargando…" : "cargar más"}
          </Button>
        </div>
      )}
    </div>
  )
}

interface FiltersProps {
  strategy: string
  setStrategy: (v: string) => void
  strategiesAvailable: string[]
  /** Resuelve el `strategy_id` técnico al nombre humano del registry.
   *  Cae al id si el registry no ha cargado o si la estrategia no está. */
  strategyLabelOf: (id: string) => string
  symbol: string
  setSymbol: (v: string) => void
  timeframe: string
  setTimeframe: (v: string) => void
  showClear: boolean
  onClear: () => void
}

function FiltersToolbar({
  strategy,
  setStrategy,
  strategiesAvailable,
  strategyLabelOf,
  symbol,
  setSymbol,
  timeframe,
  setTimeframe,
  showClear,
  onClear,
}: FiltersProps) {
  return (
    <div className="flex flex-col gap-3 rounded-md border border-border bg-card/30 p-3">
      <FilterRow label="estrategia">
        <ChipGroup
          value={strategy}
          onChange={setStrategy}
          options={strategiesAvailable}
          renderLabel={strategyLabelOf}
        />
      </FilterRow>
      <FilterRow label="símbolo">
        <ChipGroup
          value={symbol}
          onChange={setSymbol}
          options={[...WATCH_SYMBOLS]}
        />
      </FilterRow>
      <FilterRow label="timeframe">
        <ChipGroup
          value={timeframe}
          onChange={setTimeframe}
          options={[...TIMEFRAMES]}
        />
      </FilterRow>
      {showClear && (
        <Button
          variant="ghost"
          size="sm"
          onClick={onClear}
          className="self-end font-mono text-[10px] uppercase tracking-[0.14em]"
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
      <span className="min-w-[90px] font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--fg-3)]">
        {label}
      </span>
      {children}
    </div>
  )
}

function ChipGroup({
  value,
  onChange,
  options,
  renderLabel,
}: {
  value: string
  onChange: (v: string) => void
  options: string[]
  renderLabel?: (option: string) => string
}) {
  return (
    <ToggleGroup
      type="single"
      value={value}
      onValueChange={(v) => {
        // ToggleGroup type="single" devuelve "" si el user des-selecciona.
        // Tratamos eso como "todos".
        onChange(v || ANY)
      }}
      variant="outline"
      size="sm"
      spacing={1}
      className="flex-wrap"
    >
      <ToggleGroupItem
        value={ANY}
        className="font-mono text-[10px] uppercase tracking-[0.12em]"
      >
        todos
      </ToggleGroupItem>
      {options.map((opt) => (
        <ToggleGroupItem
          key={opt}
          value={opt}
          className="font-mono text-[10px] uppercase tracking-[0.12em]"
        >
          {renderLabel ? renderLabel(opt) : opt}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  )
}
