"use client"

import { useQuery } from "@tanstack/react-query"
import Link from "next/link"
import { notFound, useParams } from "next/navigation"

import { BacktestDetail } from "@/components/research/BacktestDetail"
import { Spinner } from "@/components/ui/spinner"
import { fetchBacktest } from "@/lib/core/api"

export default function BacktestDetailPage() {
  const params = useParams<{ id: string }>()
  const id = params.id
  const { data, isLoading, error } = useQuery({
    queryKey: ["backtest", id],
    queryFn: ({ signal }) => fetchBacktest(id, { signal }),
    enabled: Boolean(id),
  })

  if (error?.message?.includes("404")) notFound()

  return (
    <div className="flex flex-col gap-6">
      <Link
        href="/research/backtests"
        className="font-mono text-xs text-muted-foreground hover:text-foreground"
      >
        ← todos los backtests
      </Link>
      {isLoading && (
        <div className="flex items-center gap-2 font-mono text-xs text-muted-foreground">
          <Spinner /> cargando run…
        </div>
      )}
      {error && !error.message.includes("404") && (
        <p className="text-xs text-destructive">{String(error)}</p>
      )}
      {data && <BacktestDetail run={data} />}
    </div>
  )
}
