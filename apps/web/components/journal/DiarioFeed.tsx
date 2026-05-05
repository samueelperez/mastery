"use client"

import { useMemo } from "react"

import { SetupCard } from "@/components/journal/SetupCard"
import type { SetupListRowDTO } from "@/lib/api"
import { bucketByDay, type DayBuckets } from "@/lib/journal-rollup"

interface DiarioFeedProps {
  rows: SetupListRowDTO[]
  selectedId: string | null
  onSelect: (id: string) => void
}

interface BucketDef {
  key: keyof DayBuckets
  label: string
}

const BUCKETS: BucketDef[] = [
  { key: "today", label: "Hoy" },
  { key: "yesterday", label: "Ayer" },
  { key: "thisWeek", label: "Esta semana" },
  { key: "thisMonth", label: "Este mes" },
  { key: "earlier", label: "Anteriores" },
]

/** Feed agrupado por día. Bucketing definido en `lib/journal-rollup.ts`.
 *  Cada sección es un eyebrow + count + grid responsive de SetupCards.
 *  Si un bucket está vacío no se renderiza. Si TODOS están vacíos: empty state. */
export function DiarioFeed({ rows, selectedId, onSelect }: DiarioFeedProps) {
  const buckets = useMemo(() => bucketByDay(rows), [rows])

  const activeBuckets = BUCKETS.filter((b) => buckets[b.key].length > 0)

  if (rows.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 rounded-md border border-dashed border-border bg-card/20 px-6 py-12 text-center">
        <p className="text-[14px] font-medium text-foreground">
          Sin setups que mostrar
        </p>
        <p className="text-[13px] text-muted-foreground">
          Ajusta los filtros o pídele al copiloto un trade idea.
        </p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-8">
      {activeBuckets.map(({ key, label }) => {
        const items = buckets[key]
        return (
          <section key={key} className="flex flex-col gap-3">
            <div className="flex items-baseline justify-between border-b border-[color:var(--line-soft)] pb-1.5">
              <span className="eyebrow">{label}</span>
              <span className="font-mono text-[10px] tabular-nums text-[var(--fg-3)]">
                {items.length}
              </span>
            </div>
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2 2xl:grid-cols-3">
              {items.map((s) => (
                <SetupCard
                  key={s.id}
                  setup={s}
                  selected={selectedId === s.id}
                  onSelect={onSelect}
                  showMistakesPreview={
                    s.status === "closed" && Boolean(s.mistakes)
                  }
                />
              ))}
            </div>
          </section>
        )
      })}
    </div>
  )
}
