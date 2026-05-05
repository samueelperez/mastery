"use client"

import { useMemo, useState } from "react"

import { SetupCard } from "@/components/journal/SetupCard"
import { SetupDetailPanel } from "@/components/journal/SetupDetailPanel"
import {
  Sheet,
  SheetContent,
  SheetTitle,
} from "@/components/ui/sheet"
import type { SetupListRowDTO } from "@/lib/api"
import { formatSetupTag } from "@/lib/format-setup-tag"

import { StrategyRCurve } from "./StrategyRCurve"

interface StrategyDetailProps {
  strategy: string
  trades: SetupListRowDTO[]
}

/** Página de detalle por `setup_tag`. Diseñada para que el user vea
 *  inmediatamente "qué tal va" la estrategia: counts, curva acumulada
 *  de R y dos columnas separando aciertos vs fallos.
 *
 *  Click en cualquier card → abre `SetupDetailPanel` en un Sheet lateral
 *  (slide-from-right) con summary, events, mistakes completos. */
export function StrategyDetail({ strategy, trades }: StrategyDetailProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const { wins, losses, avgR, lastClosedAt } = useMemo(() => {
    const wins: SetupListRowDTO[] = []
    const losses: SetupListRowDTO[] = []
    let rSum = 0
    let rCount = 0
    let lastTs = 0
    for (const t of trades) {
      const r = t.r_multiple
      if (r !== null) {
        rSum += r
        rCount += 1
        if (r > 0) wins.push(t)
        else losses.push(t)
      } else {
        // Sin r_multiple es raro en closed pero por seguridad lo metemos en
        // 'losses' para no inflar el winrate. (Cancelados nunca llegan.)
        losses.push(t)
      }
      const ts = t.closed_at ? Date.parse(t.closed_at) : 0
      if (ts > lastTs) lastTs = ts
    }
    // Orden por closed_at desc dentro de cada columna.
    const byClosedDesc = (a: SetupListRowDTO, b: SetupListRowDTO) => {
      const ta = a.closed_at ? Date.parse(a.closed_at) : 0
      const tb = b.closed_at ? Date.parse(b.closed_at) : 0
      return tb - ta
    }
    wins.sort(byClosedDesc)
    losses.sort(byClosedDesc)
    return {
      wins,
      losses,
      avgR: rCount > 0 ? rSum / rCount : null,
      lastClosedAt: lastTs > 0 ? new Date(lastTs).toISOString() : null,
    }
  }, [trades])

  const total = wins.length + losses.length
  const winRatePct =
    total > 0 ? Math.round((wins.length / total) * 100) : null

  return (
    <div className="flex flex-col gap-6">
      <Hero
        strategy={strategy}
        wins={wins.length}
        losses={losses.length}
        winRatePct={winRatePct}
        avgR={avgR}
        lastClosedAt={lastClosedAt}
      />

      <StrategyRCurve trades={trades} />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Column
          title="Aciertos"
          count={wins.length}
          tone="long"
          emptyHint="aún no hay aciertos en esta estrategia."
          trades={wins}
          onSelect={setSelectedId}
          selectedId={selectedId}
        />
        <Column
          title="Fallos"
          count={losses.length}
          tone="short"
          emptyHint="ningún fallo registrado · ojalá siga así."
          trades={losses}
          onSelect={setSelectedId}
          selectedId={selectedId}
          showMistakes
        />
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
    </div>
  )
}

interface HeroProps {
  strategy: string
  wins: number
  losses: number
  winRatePct: number | null
  avgR: number | null
  lastClosedAt: string | null
}

function Hero({
  strategy,
  wins,
  losses,
  winRatePct,
  avgR,
  lastClosedAt,
}: HeroProps) {
  const total = wins + losses
  const avgTone =
    avgR === null
      ? "var(--fg-2)"
      : avgR > 0
        ? "var(--long)"
        : avgR < 0
          ? "var(--short)"
          : "var(--fg-2)"

  return (
    <header className="flex flex-col gap-3 border-b border-[color:var(--line-soft)] pb-5">
      <div className="flex items-baseline justify-between gap-4">
        <div className="flex flex-col gap-0.5">
          <h1 className="text-xl font-semibold tracking-tight text-foreground">
            {formatSetupTag(strategy)}
          </h1>
          <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
            {strategy}
          </span>
        </div>
        {lastClosedAt && (
          <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--fg-3)]">
            último cierre · {formatRelative(lastClosedAt)}
          </span>
        )}
      </div>

      {total === 0 ? (
        <p className="text-[14px] text-[var(--fg-3)]">
          aún no hay trades cerrados con esta estrategia.
        </p>
      ) : (
        <p className="text-2xl font-semibold leading-snug tracking-tight text-foreground">
          <span style={{ color: "var(--long)" }}>
            <span className="font-mono tabular-nums">{wins}</span> ganaste
          </span>
          <span className="text-[var(--fg-3)]"> · </span>
          <span style={{ color: "var(--short)" }}>
            <span className="font-mono tabular-nums">{losses}</span> fallaste
          </span>
          {winRatePct !== null && (
            <span className="text-[var(--fg-3)]">
              {" "}
              <span className="text-base font-medium">
                → <span className="font-mono tabular-nums">{winRatePct}%</span>{" "}
                acierto
              </span>
            </span>
          )}
        </p>
      )}

      {avgR !== null && (
        <p className="text-[14px] text-[var(--fg-2)]">
          promedio:{" "}
          <span
            style={{ color: avgTone }}
            className="font-mono font-medium tabular-nums text-foreground"
          >
            {avgR >= 0 ? "+" : ""}
            {avgR.toFixed(2)}R
          </span>{" "}
          por trade
        </p>
      )}
    </header>
  )
}

interface ColumnProps {
  title: string
  count: number
  tone: "long" | "short"
  emptyHint: string
  trades: SetupListRowDTO[]
  selectedId: string | null
  onSelect: (id: string) => void
  showMistakes?: boolean
}

function Column({
  title,
  count,
  tone,
  emptyHint,
  trades,
  selectedId,
  onSelect,
  showMistakes,
}: ColumnProps) {
  const accent = tone === "long" ? "var(--long)" : "var(--short)"
  return (
    <section className="flex flex-col gap-3">
      <header className="flex items-baseline justify-between border-b border-[color:var(--line-soft)] pb-1.5">
        <span
          className="font-mono text-[11px] uppercase tracking-[0.16em]"
          style={{ color: accent }}
        >
          {title}
        </span>
        <span className="font-mono text-[11px] tabular-nums text-[var(--fg-3)]">
          {count}
        </span>
      </header>
      {trades.length === 0 ? (
        <p className="rounded-md border border-dashed border-border bg-card/20 px-3 py-4 text-[12px] text-[var(--fg-3)]">
          {emptyHint}
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          {trades.map((t) => (
            <SetupCard
              key={t.id}
              setup={t}
              selected={selectedId === t.id}
              onSelect={onSelect}
              showMistakesPreview={showMistakes}
            />
          ))}
        </div>
      )}
    </section>
  )
}

function formatRelative(iso: string): string {
  const dt = new Date(iso)
  const diffMs = Date.now() - dt.getTime()
  const min = Math.round(diffMs / 60_000)
  if (min < 1) return "ahora"
  if (min < 60) return `hace ${min}m`
  const h = Math.round(min / 60)
  if (h < 24) return `hace ${h}h`
  const d = Math.round(h / 24)
  return `hace ${d}d`
}
