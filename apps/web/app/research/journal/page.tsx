"use client"

import { useQuery } from "@tanstack/react-query"

import { JournalList } from "@/components/research/JournalList"
import { fetchJournalTrades } from "@/lib/api"

export default function JournalPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["journal-trades", { limit: 100 }],
    queryFn: ({ signal }) => fetchJournalTrades({ limit: 100, signal }),
  })

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="font-mono text-sm uppercase tracking-widest text-foreground">
          journal
        </h2>
        <p className="text-xs text-muted-foreground">
          {data?.length ?? 0} trades · embedded with voyage-4-large for similarity search.
        </p>
      </div>
      <JournalList
        trades={data ?? []}
        loading={isLoading}
        error={error?.message}
      />
    </div>
  )
}
