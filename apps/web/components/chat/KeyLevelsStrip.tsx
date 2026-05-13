"use client"

import { Badge } from "@/components/ui/badge"
import type { KeyLevel } from "@/lib/chat/types"
import { cn } from "@/lib/core/utils"

interface KeyLevelsStripProps {
  levels: KeyLevel[]
  symbol: string
  className?: string
}

const kindLabel: Record<KeyLevel["kind"], string> = {
  support: "Soporte",
  resistance: "Resistencia",
  invalidation: "Invalida",
  target: "Objetivo",
  reference: "Ref.",
}

const kindCls: Record<KeyLevel["kind"], string> = {
  support: "border-[var(--long)]/40 text-[var(--long)]",
  resistance: "border-[var(--short)]/40 text-[var(--short)]",
  invalidation: "border-[var(--short)]/60 text-[var(--short)]",
  target: "border-[var(--long)]/60 text-[var(--long)]",
  reference: "border-border text-muted-foreground",
}

function formatPrice(price: number | null | undefined): string {
  // Defensa: el agente puede emitir niveles sin `price` aunque el schema
  // Pydantic lo declara como float. Mostramos "—" en lugar de crashear.
  if (typeof price !== "number" || !Number.isFinite(price)) return "—"
  if (price >= 1000) return price.toLocaleString(undefined, { maximumFractionDigits: 1 })
  if (price >= 1) return price.toLocaleString(undefined, { maximumFractionDigits: 3 })
  return price.toLocaleString(undefined, { maximumFractionDigits: 6 })
}

export function KeyLevelsStrip({ levels, className }: KeyLevelsStripProps) {
  // Filtrar niveles malformados antes de renderizar. Si el agente emitió
  // un key_level sin `price` o con NaN, lo descartamos en lugar de
  // mostrar "—" que confunde al user.
  const valid = levels.filter(
    (lvl) =>
      typeof lvl.price === "number" &&
      Number.isFinite(lvl.price) &&
      typeof lvl.label === "string" &&
      lvl.label.length > 0,
  )
  if (valid.length === 0) return null
  return (
    <div className={cn("flex flex-wrap items-center gap-1.5", className)}>
      {valid.map((lvl, i) => (
        <Badge
          key={`${lvl.kind}-${lvl.price}-${i}`}
          variant="outline"
          className={cn(
            "gap-1.5 px-2 py-0.5 font-mono text-[11px] tabular-nums",
            kindCls[lvl.kind],
          )}
        >
          <span className="text-[10px] uppercase tracking-[0.1em] opacity-80">
            {kindLabel[lvl.kind]}
          </span>
          <span className="text-foreground/90">{lvl.label}</span>
          <span className="text-foreground">{formatPrice(lvl.price)}</span>
        </Badge>
      ))}
    </div>
  )
}
