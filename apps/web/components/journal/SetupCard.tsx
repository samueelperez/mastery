"use client"

import { ArrowDownIcon, ArrowUpIcon, CheckIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import type { SetupListRowDTO, SetupStatus } from "@/lib/api"
import { formatSetupTag } from "@/lib/format-setup-tag"
import { cn } from "@/lib/utils"

interface SetupCardProps {
  setup: SetupListRowDTO
  selected?: boolean
  onSelect?: (id: string) => void
  /** Si true y `setup.mistakes` no es null, muestra una preview de 1 línea
   *  debajo del R-multiple. Útil en la página de detalle de estrategia
   *  para ver el "por qué" de los fallos sin abrir el panel. */
  showMistakesPreview?: boolean
}

const STATUS_LABEL: Record<SetupStatus, string> = {
  pending: "esperando",
  active: "activo",
  closed: "cerrado",
  cancelled: "cancelado",
}

const STATUS_CLS: Record<SetupStatus, string> = {
  pending: "border-[var(--violet)]/40 text-[var(--violet)]",
  active: "border-[var(--long)]/40 text-[var(--long)]",
  closed: "border-border text-muted-foreground",
  cancelled: "border-border text-muted-foreground/70",
}

const SIDE_TINT: Record<SetupListRowDTO["side"], string> = {
  long: "border-l-2 border-[var(--long)]/60",
  short: "border-l-2 border-[var(--short)]/60",
}

function formatPrice(price: number): string {
  if (price >= 1000) return price.toLocaleString(undefined, { maximumFractionDigits: 1 })
  if (price >= 1) return price.toLocaleString(undefined, { maximumFractionDigits: 3 })
  return price.toLocaleString(undefined, { maximumFractionDigits: 6 })
}

function formatRelativeTime(iso: string | null): string {
  if (!iso) return ""
  const dt = new Date(iso)
  const diffMs = Date.now() - dt.getTime()
  const diffMin = Math.round(diffMs / 60_000)
  if (diffMin < 1) return "ahora"
  if (diffMin < 60) return `hace ${diffMin}m`
  const diffH = Math.round(diffMin / 60)
  if (diffH < 24) return `hace ${diffH}h`
  const diffD = Math.round(diffH / 24)
  return `hace ${diffD}d`
}

export function SetupCard({
  setup,
  selected,
  onSelect,
  showMistakesPreview,
}: SetupCardProps) {
  const sideIcon =
    setup.side === "long" ? (
      <ArrowUpIcon className="size-3.5 text-[var(--long)]" aria-hidden />
    ) : (
      <ArrowDownIcon className="size-3.5 text-[var(--short)]" aria-hidden />
    )
  const sideLabel = setup.side === "long" ? "LONG" : "SHORT"
  const targetsHit = setup.targets.filter((t) => t.hit_at).length
  const targetsTotal = setup.targets.length
  const proposedRel = formatRelativeTime(setup.proposed_at)
  const r = setup.r_multiple

  return (
    <Card
      role="button"
      tabIndex={0}
      onClick={() => onSelect?.(setup.id)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault()
          onSelect?.(setup.id)
        }
      }}
      className={cn(
        "cursor-pointer transition-colors hover:bg-[var(--bg-2)]/40",
        SIDE_TINT[setup.side],
        selected && "ring-1 ring-ring",
      )}
    >
      <CardHeader className="gap-1 pb-2">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5">
            {sideIcon}
            <span className="font-mono text-[12px] font-semibold tracking-tight text-foreground">
              {setup.symbol}
            </span>
            <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
              · {setup.timeframe} · {sideLabel}
            </span>
          </div>
          <Badge
            variant="outline"
            className={cn(
              "font-mono text-[10px] uppercase tracking-[0.12em]",
              STATUS_CLS[setup.status],
            )}
          >
            {STATUS_LABEL[setup.status]}
          </Badge>
        </div>
        <div className="flex items-center justify-between gap-2 font-mono text-[10px] tabular-nums text-muted-foreground">
          <span>{formatSetupTag(setup.setup_tag)}</span>
          <span>{proposedRel}</span>
        </div>
      </CardHeader>
      <CardContent className="pt-0 pb-3">
        <ul className="flex flex-col gap-0.5 font-mono text-[11px] tabular-nums">
          <li className="flex items-center justify-between">
            <span className="text-[var(--fg-3)]">entry</span>
            <span className="text-foreground">
              {formatPrice(setup.entry_px)}
            </span>
          </li>
          {setup.invalidation_px !== null && (
            <li className="flex items-center justify-between">
              <span className="text-[var(--fg-3)]">SL</span>
              <span className="text-[var(--short)]">
                {formatPrice(setup.invalidation_px)}
              </span>
            </li>
          )}
          {setup.targets.length > 0 && (
            <li className="flex items-center justify-between">
              <span className="text-[var(--fg-3)]">
                TPs ({targetsHit}/{targetsTotal})
              </span>
              <span className="text-[var(--long)]">
                {setup.targets.map((t) => formatPrice(t.price)).join(" · ")}
              </span>
            </li>
          )}
          {r !== null && (
            <li className="flex items-center justify-between">
              <span className="text-[var(--fg-3)]">R-multiple</span>
              <span
                className={cn(
                  "tabular-nums",
                  r > 0
                    ? "text-[var(--long)]"
                    : r < 0
                      ? "text-[var(--short)]"
                      : "text-foreground",
                )}
              >
                {r >= 0 ? "+" : ""}
                {r.toFixed(2)}
              </span>
            </li>
          )}
        </ul>
        {setup.status === "active" && setup.entry_hit_at && (
          <div className="mt-2 flex items-center gap-1 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--long)]">
            <CheckIcon className="size-3" aria-hidden />
            entry tocado {formatRelativeTime(setup.entry_hit_at)}
          </div>
        )}
        {showMistakesPreview && setup.mistakes && (
          <p className="mt-2 line-clamp-2 text-[11px] leading-snug text-[var(--fg-3)]">
            {setup.mistakes}
          </p>
        )}
      </CardContent>
    </Card>
  )
}
