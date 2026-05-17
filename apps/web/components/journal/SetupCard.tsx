"use client"

import { ArrowDownIcon, ArrowUpIcon, CheckIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { SetupListRowDTO, SetupStatus } from "@/lib/core/api"
import { formatSetupTag } from "@/lib/journal/format-setup-tag"
import { cn } from "@/lib/core/utils"

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

interface StatusReadout {
  label: string
  cls: string
  tooltip: string
}

/** Deriva la causa terminal del setup directamente desde los campos del
 *  `SetupListRowDTO` (sin necesidad de cargar events). Diferencia las
 *  cuatro causas reales de "cancelled" (auto-invalidated / expired /
 *  manual cancel) y las tres de "closed" (TP / SL / breakeven o cierre
 *  manual). Cada causa lleva su propio tooltip para que el usuario
 *  entienda el "por qué" sin abrir el panel. */
function deriveStatusReadout(setup: SetupListRowDTO): StatusReadout {
  const base = STATUS_LABEL[setup.status]
  const baseCls = STATUS_CLS[setup.status]
  if (setup.status === "cancelled") {
    if (setup.invalidated_at) {
      return {
        label: "invalidado",
        cls: "border-[var(--short)]/35 text-[var(--short)]/85",
        tooltip:
          "Auto-invalidado por una invalidation_condition antes de entrar — el setup pre-entry violó su tesis. Abre el detalle para ver qué condición disparó.",
      }
    }
    if (setup.expires_at && new Date(setup.expires_at).getTime() <= Date.now()) {
      return {
        label: "expirado",
        cls: "border-[var(--amber)]/45 text-[var(--amber)]/90",
        tooltip:
          "Wall-clock expires_at venció antes de entry hit. El setup era time-sensitive y la ventana cerró.",
      }
    }
    return {
      label: base,
      cls: baseCls,
      tooltip:
        "Cancelado manualmente por el usuario. Sin discriminador automático.",
    }
  }
  if (setup.status === "closed") {
    const r = setup.r_multiple
    if (r !== null && r > 0.2) {
      return {
        label: "TP hit",
        cls: "border-[var(--long)]/40 text-[var(--long)]",
        tooltip: `Cerrado en TP con R-multiple ${r >= 0 ? "+" : ""}${r.toFixed(2)}.`,
      }
    }
    if (r !== null && r < 0) {
      return {
        label: "SL hit",
        cls: "border-[var(--short)]/40 text-[var(--short)]",
        tooltip: `Cerrado en SL con R-multiple ${r.toFixed(2)}.`,
      }
    }
    return {
      label: base,
      cls: baseCls,
      tooltip:
        "Cerrado en breakeven o por cierre manual / time stop. Abre el detalle para ver el motivo.",
    }
  }
  return {
    label: base,
    cls: baseCls,
    tooltip:
      setup.status === "pending"
        ? "Esperando entry hit. Las invalidation_conditions se evalúan en cada candle close."
        : "Setup activo — entry tocado, watching SL/TPs.",
  }
}

const SIDE_TINT: Record<SetupListRowDTO["side"], string> = {
  long: "border-l-2 border-[var(--long)]/60",
  short: "border-l-2 border-[var(--short)]/60",
}

function formatPrice(price: number | null | undefined): string {
  if (price == null || !Number.isFinite(price)) return "—"
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
  const statusReadout = deriveStatusReadout(setup)

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
          <Tooltip>
            <TooltipTrigger asChild>
              <Badge
                variant="outline"
                onClick={(e) => e.stopPropagation()}
                className={cn(
                  "cursor-help font-mono text-[10px] uppercase tracking-[0.12em]",
                  statusReadout.cls,
                )}
              >
                {statusReadout.label}
              </Badge>
            </TooltipTrigger>
            <TooltipContent>
              <span className="text-[11px]">{statusReadout.tooltip}</span>
            </TooltipContent>
          </Tooltip>
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
          {setup.stop_loss_px != null && (
            <li className="flex items-center justify-between">
              <span className="text-[var(--fg-3)]">SL</span>
              <span className="text-[var(--short)]">
                {formatPrice(setup.stop_loss_px)}
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
          /* Hover muestra la lección completa sin truncar — las lessons son
           *  el output más valioso del post-mortem y no deberían estar
           *  cortadas por line-clamp. Card sigue compacta (1 línea preview)
           *  pero el detalle es 1 click away. */
          <HoverCard openDelay={120} closeDelay={80}>
            <HoverCardTrigger asChild>
              <p
                onClick={(e) => e.stopPropagation()}
                className="mt-2 cursor-help truncate text-[11px] leading-snug text-[var(--fg-3)] hover:text-[var(--fg-2)]"
                title="hover para ver la lección completa"
              >
                {setup.mistakes}
              </p>
            </HoverCardTrigger>
            <HoverCardContent
              className="w-80 p-3"
              onClick={(e) => e.stopPropagation()}
            >
              <p className="text-[11px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
                lección del post-mortem
              </p>
              <p className="mt-1 whitespace-pre-line text-[12px] leading-relaxed text-foreground/85">
                {setup.mistakes}
              </p>
            </HoverCardContent>
          </HoverCard>
        )}
      </CardContent>
    </Card>
  )
}
