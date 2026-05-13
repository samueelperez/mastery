"use client"

import { cn } from "@/lib/core/utils"
import {
  TIMEFRAMES,
  WATCH_SYMBOLS,
  useActiveSymbol,
  type Timeframe,
  type WatchSymbol,
} from "@/lib/store/active-symbol"

import { CryptoLogo } from "./CryptoLogo"

/** Sidebar de la dashboard con la watchlist + selector de timeframe.
 *
 * Patrón:
 *  - Desktop (lg+): columna sticky 200px a la izquierda del chart.
 *  - Mobile: row horizontal scrolleable con los mismos elementos.
 *
 * Es un componente dumb: lee `useActiveSymbol` y dispara `setSymbol` /
 * `setTimeframe`. El sync con URL lo hace `useActiveSymbolUrlSync` montado
 * en page.tsx.
 */
export function SymbolSidebar() {
  const { symbol, timeframe, setSymbol, setTimeframe } = useActiveSymbol()

  return (
    <aside
      aria-label="watchlist y timeframe"
      className={cn(
        "flex min-h-0 flex-col gap-3 rounded-lg border border-border bg-card p-3",
      )}
    >
      <div className="flex items-center justify-between">
        <span className="eyebrow">watchlist</span>
        <span className="font-mono text-[10px] tabular-nums text-[var(--fg-3)]">
          {WATCH_SYMBOLS.length} símbolos
        </span>
      </div>

      <ul className="flex flex-row gap-1.5 overflow-x-auto lg:flex-col lg:overflow-visible">
        {WATCH_SYMBOLS.map((s) => (
          <li key={s} className="shrink-0 lg:shrink">
            <SymbolRow
              symbol={s}
              active={s === symbol}
              onSelect={() => setSymbol(s)}
            />
          </li>
        ))}
      </ul>

      <div className="mt-auto hidden flex-col gap-1.5 lg:flex">
        <span className="eyebrow">timeframe</span>
        <div className="flex flex-wrap gap-1">
          {TIMEFRAMES.map((tf) => (
            <TfPill
              key={tf}
              tf={tf}
              active={tf === timeframe}
              onSelect={() => setTimeframe(tf)}
            />
          ))}
        </div>
      </div>

      {/* Mobile: tf inline */}
      <div className="flex flex-wrap gap-1 lg:hidden">
        {TIMEFRAMES.map((tf) => (
          <TfPill
            key={tf}
            tf={tf}
            active={tf === timeframe}
            onSelect={() => setTimeframe(tf)}
          />
        ))}
      </div>
    </aside>
  )
}

interface SymbolRowProps {
  symbol: WatchSymbol
  active: boolean
  onSelect: () => void
}

function SymbolRow({ symbol, active, onSelect }: SymbolRowProps) {
  const baseSym = symbol.replace(/USDT$/, "")
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={active}
      aria-current={active ? "true" : undefined}
      className={cn(
        "group flex w-full items-center gap-2.5 rounded-md border border-transparent px-2.5 py-2",
        "transition-colors duration-150 ease-out",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2",
        active
          ? "bg-[var(--violet-soft)] text-foreground shadow-[inset_0_0_0_1px_oklch(0.55_0.16_290_/_0.5)]"
          : "text-[var(--fg-2)] hover:bg-[var(--bg-2)] hover:text-foreground",
      )}
    >
      <CryptoLogo symbol={symbol} size={22} />
      <div className="flex min-w-0 flex-1 flex-col items-start text-left">
        <span className="font-mono text-[12px] font-medium tracking-tight">
          {baseSym}
        </span>
        <span className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-[var(--fg-3)]">
          usdt-m
        </span>
      </div>
      {active && (
        <span
          aria-hidden
          className="dot dot-violet ml-auto"
        />
      )}
    </button>
  )
}

interface TfPillProps {
  tf: Timeframe
  active: boolean
  onSelect: () => void
}

function TfPill({ tf, active, onSelect }: TfPillProps) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={active}
      className={cn(
        "rounded border px-2 py-1 font-mono text-[10px] uppercase tracking-[0.12em] tabular-nums",
        "transition-colors duration-150",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-1",
        active
          ? "border-[oklch(0.55_0.16_290_/_0.5)] bg-[var(--violet-soft)] text-[var(--violet)]"
          : "border-border bg-[var(--bg-2)] text-[var(--fg-2)] hover:bg-[var(--bg-3)] hover:text-foreground",
      )}
    >
      {tf}
    </button>
  )
}
