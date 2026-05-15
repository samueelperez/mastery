"use client"

import { LayersIcon, PlusIcon, XIcon } from "lucide-react"

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { cn } from "@/lib/core/utils"
import {
  countActiveLayers,
  useChartOverlays,
} from "@/lib/store/chart-overlays"

interface OverlayPanelProps {
  symbol: string
}

/** Mini-panel en el header del LiveChart. SIEMPRE visible:
 *   - count > 0  → pill `N capas` (estilo info).
 *   - count = 0  → botón discreto `+ indicadores`.
 *
 *  El popover tiene dos secciones:
 *   - Indicadores (preference user-level, persiste en localStorage).
 *   - Del agente (ephemeral: structure y TradeIdea, lifecycle del chat).
 */
export function OverlayPanel({ symbol }: OverlayPanelProps) {
  const bundle = useChartOverlays((s) => s.bySymbol[symbol])
  const minimalMode = useChartOverlays((s) => s.minimalMode)
  const toggleEma = useChartOverlays((s) => s.toggleEma)
  const toggleSma = useChartOverlays((s) => s.toggleSma)
  const toggleBbands = useChartOverlays((s) => s.toggleBbands)
  const toggleVwap = useChartOverlays((s) => s.toggleVwap)
  const setStructure = useChartOverlays((s) => s.setStructure)
  const removeTradeIdea = useChartOverlays((s) => s.removeTradeIdea)
  const clearAgent = useChartOverlays((s) => s.clearAgent)
  const clear = useChartOverlays((s) => s.clear)
  const toggleMinimalMode = useChartOverlays((s) => s.toggleMinimalMode)

  const count = countActiveLayers(bundle)
  const ind = bundle?.indicators
  const ideas = bundle?.tradeIdeas ?? []
  const hasAgent = Boolean(bundle?.structure || ideas.length > 0)

  return (
    <Popover>
      <PopoverTrigger asChild>
        {count > 0 ? (
          <button
            type="button"
            aria-label={`${count} capas activas`}
            className={cn(
              "pill-status pill-status-info",
              "transition-colors hover:brightness-110",
              "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2",
            )}
          >
            <LayersIcon className="size-3" aria-hidden />
            <span className="tabular">{count} capas</span>
          </button>
        ) : (
          <button
            type="button"
            aria-label="añadir indicadores"
            className={cn(
              "inline-flex items-center gap-1.5 rounded border border-border bg-[var(--bg-2)] px-2 py-1",
              "font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--fg-2)]",
              "transition-colors hover:bg-[var(--bg-3)] hover:text-foreground",
              "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2",
            )}
          >
            <PlusIcon className="size-3" aria-hidden />
            indicadores
          </button>
        )}
      </PopoverTrigger>
      <PopoverContent
        align="end"
        sideOffset={6}
        className="w-80 p-0 font-mono"
      >
        <div className="flex items-center justify-between border-b border-border px-3 py-2">
          <span className="eyebrow">capas · {symbol}</span>
          {hasAgent && (
            <button
              type="button"
              onClick={() => clearAgent(symbol)}
              className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--fg-3)] transition-colors hover:text-foreground"
            >
              limpiar agente
            </button>
          )}
        </div>

        <div className="border-b border-[color:var(--line-soft)] px-3 py-2">
          <p className="eyebrow mb-2">indicadores</p>
          <div className="flex flex-wrap gap-1.5">
            <Chip
              label="EMA 21"
              dotClass="bg-[var(--amber)]"
              active={ind?.ema.includes(21)}
              onClick={() => toggleEma(symbol, 21)}
            />
            <Chip
              label="EMA 55"
              dotClass="bg-[var(--violet)]"
              active={ind?.ema.includes(55)}
              onClick={() => toggleEma(symbol, 55)}
            />
            <Chip
              label="EMA 200"
              dotClass="bg-[var(--fg-3)]"
              active={ind?.ema.includes(200)}
              onClick={() => toggleEma(symbol, 200)}
            />
            <Chip
              label="SMA 50"
              dotClass="bg-[var(--long)]"
              active={ind?.sma.includes(50)}
              onClick={() => toggleSma(symbol, 50)}
            />
            <Chip
              label="SMA 100"
              dotClass="bg-[var(--violet)]"
              active={ind?.sma.includes(100)}
              onClick={() => toggleSma(symbol, 100)}
            />
            <Chip
              label="BB 20"
              dotClass="bg-[var(--fg-4)]"
              active={ind?.bbands}
              onClick={() => toggleBbands(symbol)}
            />
            <Chip
              label="VWAP"
              dotClass="bg-[var(--long)]"
              active={ind?.vwap}
              onClick={() => toggleVwap(symbol)}
            />
          </div>
          {/* EMAs/SMAs custom que el agente haya añadido fuera de los presets */}
          {ind && (
            <CustomIndicators
              ema={ind.ema.filter((p) => ![21, 55, 200].includes(p))}
              sma={ind.sma.filter((p) => ![50, 100].includes(p))}
              onRemoveEma={(p) => toggleEma(symbol, p)}
              onRemoveSma={(p) => toggleSma(symbol, p)}
            />
          )}
        </div>

        <div className="px-3 py-2">
          <p className="eyebrow mb-2">del agente</p>
          {!hasAgent && (
            <p className="font-mono text-[10px] text-[var(--fg-3)]">
              vacío. pídele un análisis al copiloto.
            </p>
          )}
          <div className="flex flex-col divide-y divide-[color:var(--line-soft)]">
            {bundle?.structure && (
              <LayerRow
                dotClass="bg-[var(--violet)]"
                label="Structure (S/R + pivots)"
                detail={`${bundle.structure.tf} · ${bundle.structure.support.length}S + ${bundle.structure.resistance.length}R · ${bundle.structure.trendLabel}`}
                onRemove={() => setStructure(symbol, null)}
              />
            )}
            {ideas.map((idea, i) => (
              <LayerRow
                key={idea.id}
                dotClass={
                  idea.direction === "long"
                    ? "bg-[var(--long)]"
                    : "bg-[var(--short)]"
                }
                label={`${idea.direction === "long" ? "Long" : "Short"} idea${ideas.length > 1 ? ` ${i + 1}/${ideas.length}` : ""}`}
                detail={`entry ${idea.entry.toFixed(2)} · SL ${idea.stopLoss.toFixed(2)} · ${idea.targets.length} TPs`}
                onRemove={() => removeTradeIdea(symbol, idea.id)}
              />
            ))}
          </div>
        </div>

        <div className="flex items-center justify-between border-t border-border px-3 py-2">
          <button
            type="button"
            onClick={toggleMinimalMode}
            className={cn(
              "inline-flex items-center gap-1.5 rounded px-2 py-1",
              "font-mono text-[10px] uppercase tracking-[0.12em]",
              "transition-colors",
              minimalMode
                ? "bg-[var(--violet-soft)] text-[var(--violet)]"
                : "text-[var(--fg-3)] hover:bg-[var(--bg-2)] hover:text-foreground",
            )}
            aria-pressed={minimalMode}
          >
            <span
              aria-hidden
              className={cn(
                "size-2 rounded-sm border",
                minimalMode
                  ? "border-[var(--violet)] bg-[var(--violet)]"
                  : "border-[var(--fg-4)]",
              )}
            />
            modo minimalista
          </button>
          <button
            type="button"
            onClick={() => clear(symbol)}
            className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--fg-3)] transition-colors hover:text-[var(--short)]"
          >
            limpiar todo
          </button>
        </div>
      </PopoverContent>
    </Popover>
  )
}

interface ChipProps {
  label: string
  dotClass: string
  active: boolean | undefined
  onClick: () => void
}

function Chip({ label, dotClass, active, onClick }: ChipProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "inline-flex items-center gap-1.5 rounded border px-2 py-1",
        "font-mono text-[10px] uppercase tracking-[0.1em] tabular",
        "transition-colors duration-150",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-1",
        active
          ? "border-[oklch(0.55_0.16_290_/_0.5)] bg-[var(--violet-soft)] text-foreground shadow-[inset_0_0_0_1px_oklch(0.55_0.16_290_/_0.3)]"
          : "border-border bg-[var(--bg-2)] text-[var(--fg-2)] hover:bg-[var(--bg-3)] hover:text-foreground",
      )}
    >
      <span aria-hidden className={cn("dot", dotClass)} />
      {label}
    </button>
  )
}

interface CustomIndicatorsProps {
  ema: number[]
  sma: number[]
  onRemoveEma: (period: number) => void
  onRemoveSma: (period: number) => void
}

function CustomIndicators({
  ema,
  sma,
  onRemoveEma,
  onRemoveSma,
}: CustomIndicatorsProps) {
  if (ema.length === 0 && sma.length === 0) return null
  return (
    <div className="mt-2 flex flex-wrap gap-1.5 border-t border-[color:var(--line-soft)] pt-2">
      {ema.map((p) => (
        <CustomChip
          key={`ema-${p}`}
          label={`EMA ${p}`}
          onRemove={() => onRemoveEma(p)}
        />
      ))}
      {sma.map((p) => (
        <CustomChip
          key={`sma-${p}`}
          label={`SMA ${p}`}
          onRemove={() => onRemoveSma(p)}
        />
      ))}
    </div>
  )
}

interface CustomChipProps {
  label: string
  onRemove: () => void
}

function CustomChip({ label, onRemove }: CustomChipProps) {
  return (
    <span className="inline-flex items-center gap-1 rounded border border-[oklch(0.55_0.16_290_/_0.5)] bg-[var(--violet-soft)] px-2 py-1 font-mono text-[10px] uppercase tracking-[0.1em] tabular text-[var(--violet)]">
      {label}
      <button
        type="button"
        onClick={onRemove}
        aria-label={`quitar ${label}`}
        className="grid size-3 place-items-center text-[var(--violet)] transition-colors hover:text-[var(--short)]"
      >
        <XIcon className="size-2.5" aria-hidden />
      </button>
    </span>
  )
}

interface LayerRowProps {
  dotClass: string
  label: string
  detail: string
  onRemove: () => void
}

function LayerRow({ dotClass, label, detail, onRemove }: LayerRowProps) {
  return (
    <div className="flex items-center gap-2.5 py-2">
      <span aria-hidden className={cn("dot", dotClass)} />
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="font-mono text-[12px] text-foreground">{label}</span>
        <span className="font-mono text-[10px] tracking-[0.06em] text-[var(--fg-3)]">
          {detail}
        </span>
      </div>
      <button
        type="button"
        onClick={onRemove}
        aria-label={`quitar ${label}`}
        className="grid size-6 place-items-center rounded text-[var(--fg-3)] transition-colors hover:bg-[var(--bg-3)] hover:text-[var(--short)]"
      >
        <XIcon className="size-3" aria-hidden />
      </button>
    </div>
  )
}
