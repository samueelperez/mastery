"use client"

import { useQuery } from "@tanstack/react-query"
import { useMemo } from "react"

import { BestStrategyHero } from "@/components/research/BestStrategyHero"
import { StrategyWinrateTable } from "@/components/research/StrategyWinrateTable"
import { Skeleton } from "@/components/ui/skeleton"
import { fetchStrategyWinrate, type StrategyWinrateDTO } from "@/lib/core/api"
import { pickBestStrategy } from "@/lib/research/strategy-rank"

export default function StrategiesPage() {
  const { data, isLoading, error } = useQuery<StrategyWinrateDTO[]>({
    queryKey: ["strategy-winrate", { minN: 1 }],
    queryFn: ({ signal }) => fetchStrategyWinrate({ minN: 1, signal }),
    staleTime: 30_000,
  })

  const rows = data ?? []
  const best = useMemo(() => pickBestStrategy(rows), [rows])

  return (
    <div className="flex flex-col gap-6">
      {isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : (
        <BestStrategyHero strategy={best} totalStrategies={rows.length} />
      )}

      <div>
        <h2 className="text-xl font-semibold tracking-tight text-foreground">
          Todas las estrategias
        </h2>
        <p className="text-[13px] text-muted-foreground">
          {rows.length} estrategia(s) sobre trades cerrados · click en una
          para ver sus aciertos y fallos.
        </p>
      </div>

      <StrategyWinrateTable
        rows={rows}
        loading={isLoading}
        error={error?.message}
      />
    </div>
  )
}
