"use client"

import { useQuery } from "@tanstack/react-query"
import { useMemo } from "react"

import { EdgeStateTiers } from "@/components/research/EdgeStateTiers"
import { ResumenHero } from "@/components/research/ResumenHero"
import { Skeleton } from "@/components/ui/skeleton"
import {
  fetchBacktests,
  fetchSetups,
  type BacktestRunSummaryDTO,
  type SetupListResponseDTO,
} from "@/lib/core/api"
import { tierStrategies } from "@/lib/research/edge-state"
import { computeOverviewKpi } from "@/lib/research/rollup"

export default function ResearchOverviewPage() {
  const backtests = useQuery<BacktestRunSummaryDTO[]>({
    queryKey: ["backtests-overview", { limit: 100 }],
    queryFn: ({ signal }) => fetchBacktests({ limit: 100, signal }),
    staleTime: 60_000,
  })

  // Cerrados de cualquier source (agente + importados + paper + live).
  // El backend trata `source: ""` como "sin filtro de source".
  const closedSetups = useQuery<SetupListResponseDTO>({
    queryKey: ["closed-setups-overview", { limit: 200 }],
    queryFn: ({ signal }) =>
      fetchSetups({
        source: "",
        status: "closed",
        limit: 200,
        signal,
      }),
    staleTime: 30_000,
  })

  const tiers = useMemo(
    () => tierStrategies(backtests.data ?? []),
    [backtests.data],
  )
  const totalClassified =
    tiers.strong.length + tiers.marginal.length + tiers.weak.length

  const kpi = useMemo(
    () => computeOverviewKpi(closedSetups.data?.rows ?? []),
    [closedSetups.data?.rows],
  )

  const heroLoading = closedSetups.isLoading
  const tiersLoading = backtests.isLoading

  return (
    <div className="flex flex-col gap-6">
      {heroLoading ? (
        <Skeleton className="h-44 w-full" />
      ) : (
        <ResumenHero kpi={kpi} />
      )}

      {tiersLoading ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      ) : (
        <EdgeStateTiers tiers={tiers} totalClassified={totalClassified} />
      )}
    </div>
  )
}
