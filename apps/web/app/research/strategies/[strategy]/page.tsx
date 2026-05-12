"use client"

import { useQuery } from "@tanstack/react-query"
import Link from "next/link"
import { useParams } from "next/navigation"

import { StrategyDetail } from "@/components/research/StrategyDetail"
import { Spinner } from "@/components/ui/spinner"
import { fetchSetups, type SetupListResponseDTO } from "@/lib/core/api"

export default function StrategyDetailPage() {
  const params = useParams<{ strategy: string }>()
  const strategy = params.strategy ? decodeURIComponent(params.strategy) : ""

  // No filtramos por `source`: el listado de winrate (`/strategies/winrate`)
  // agrega trades cerrados de cualquier source (manual_log, agent_proposal,
  // paper, live), así que aquí debemos hacer lo mismo o aparecen estrategias
  // en la tabla cuyo detalle dice "sin trades".
  const { data, isLoading, error } = useQuery<SetupListResponseDTO>({
    queryKey: ["strategy-detail", strategy],
    queryFn: ({ signal }) =>
      fetchSetups({
        setupTag: strategy,
        status: "closed",
        source: "",
        limit: 200,
        signal,
      }),
    enabled: Boolean(strategy),
    staleTime: 30_000,
  })

  return (
    <div className="flex flex-col gap-6">
      <Link
        href="/research/strategies"
        className="text-[13px] text-muted-foreground transition-colors hover:text-foreground"
      >
        ← todas las estrategias
      </Link>

      {isLoading && (
        <div className="flex items-center gap-2 text-[13px] text-muted-foreground">
          <Spinner /> cargando trades…
        </div>
      )}

      {error && (
        <p className="text-[13px] text-destructive">
          Error: {error.message}
        </p>
      )}

      {data && <StrategyDetail strategy={strategy} trades={data.rows} />}
    </div>
  )
}
