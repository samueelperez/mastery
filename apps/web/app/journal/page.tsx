"use client"

import { useQuery } from "@tanstack/react-query"
import { useMemo, useState } from "react"

import { DiarioFeed } from "@/components/journal/DiarioFeed"
import {
  DiarioFilters,
  type SourceFilter,
  type StatusFilter,
} from "@/components/journal/DiarioFilters"
import { DiarioHero } from "@/components/journal/DiarioHero"
import { SetupDetailPanel } from "@/components/journal/SetupDetailPanel"
import {
  Sheet,
  SheetContent,
  SheetTitle,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import {
  fetchSetups,
  type SetupListResponseDTO,
  type SetupStatus,
} from "@/lib/core/api"
import { computeStreak, computeWeekStats } from "@/lib/journal/rollup"

export default function JournalPage() {
  const [source, setSource] = useState<SourceFilter>("agent")
  const [status, setStatus] = useState<StatusFilter>("all")
  const [symbol, setSymbol] = useState<string>("")
  const [selectedId, setSelectedId] = useState<string | null>(null)

  // Backend toma `source: ""` como "todas las fuentes" y "agent_proposal"
  // como filtro a setups del agente. Status `all` lo tratamos client-side
  // sobre el array que devuelve la API (que viene sin filtro de status).
  const apiSource = source === "agent" ? "agent_proposal" : ""
  const apiStatus: SetupStatus | undefined =
    status === "all" ? undefined : status

  const { data, isLoading, error } = useQuery<SetupListResponseDTO>({
    queryKey: [
      "diario-setups",
      { source: apiSource, status: apiStatus, symbol },
    ],
    queryFn: ({ signal }) =>
      fetchSetups({
        source: apiSource,
        status: apiStatus,
        symbol: symbol || undefined,
        limit: 200,
        signal,
      }),
    refetchInterval: 60_000,
    staleTime: 30_000,
  })

  const rows = data?.rows ?? []
  const counts = data?.counts

  // Para el hero (week stats + streak) queremos SIEMPRE el universo completo
  // del scope (source/symbol activos), no el subset filtrado por status.
  // En la práctica `apiStatus=undefined` ya devuelve todo; pero si el user
  // filtra por status, mantenemos el rollup sobre `rows` igual — es menor
  // pero refleja "lo que ves". Trade-off aceptable: si filtras por
  // 'esperando', el hero se vacía (lógico, no hay cierres en pending).
  const weekStats = useMemo(() => computeWeekStats(rows), [rows])
  const streak = useMemo(() => computeStreak(rows), [rows])

  const filtersActive =
    source !== "agent" || status !== "all" || symbol !== ""
  const clearFilters = () => {
    setSource("agent")
    setStatus("all")
    setSymbol("")
  }

  return (
    <>
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6">
        <header className="flex flex-col gap-1">
          <h1 className="text-xl font-semibold tracking-tight text-foreground">
            Diario
          </h1>
          <p className="text-[13px] text-muted-foreground">
            Setups del agente con seguimiento automático y trades importados
            en una sola vista.
          </p>
        </header>

        {isLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : (
          <DiarioHero weekStats={weekStats} streak={streak} />
        )}

        <DiarioFilters
          source={source}
          setSource={setSource}
          status={status}
          setStatus={setStatus}
          symbol={symbol}
          setSymbol={setSymbol}
          counts={counts}
          showClear={filtersActive}
          onClear={clearFilters}
        />

        {error && (
          <p className="text-[13px] text-destructive">
            Error: {(error as Error).message}
          </p>
        )}

        {isLoading ? (
          <div className="flex flex-col gap-4">
            <Skeleton className="h-44 w-full" />
            <Skeleton className="h-44 w-full" />
          </div>
        ) : (
          <DiarioFeed
            rows={rows}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
        )}
      </div>

      <Sheet
        open={selectedId !== null}
        onOpenChange={(o) => {
          if (!o) setSelectedId(null)
        }}
      >
        <SheetContent
          side="right"
          showCloseButton={false}
          className="w-full p-0 sm:max-w-md"
        >
          <SheetTitle className="sr-only">Detalle del setup</SheetTitle>
          <SetupDetailPanel
            setupId={selectedId}
            onClose={() => setSelectedId(null)}
          />
        </SheetContent>
      </Sheet>
    </>
  )
}
