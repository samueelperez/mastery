"use client"

import { useQuery } from "@tanstack/react-query"

import { BacktestList } from "@/components/research/BacktestList"
import { fetchBacktests } from "@/lib/api"

export default function BacktestsListPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["backtests", { limit: 100 }],
    queryFn: ({ signal }) => fetchBacktests({ limit: 100, signal }),
  })

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="font-mono text-sm uppercase tracking-widest text-foreground">
          todos los backtests
        </h2>
        <p className="text-xs text-muted-foreground">
          {data?.length ?? 0} runs · ordenados por created_at desc.
        </p>
      </div>
      <BacktestList
        runs={data ?? []}
        loading={isLoading}
        error={error?.message}
      />
    </div>
  )
}
