"use client"

import { PowerIcon, Trash2Icon } from "lucide-react"
import { useState } from "react"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import type { AlertRuleDTO } from "@/lib/core/api"
import { formatCooldown, summarizeAlertConditions } from "@/lib/core/format"
import { cn } from "@/lib/core/utils"

interface RuleCardProps {
  rule: AlertRuleDTO
  onToggle: (rule: AlertRuleDTO) => void
  onDelete: (rule: AlertRuleDTO) => void
  isPending?: boolean
}

/** Card visual de una regla de alerta. Sustituye la fila de tabla del
 *  diseño anterior. Header con nombre + status; eyebrow técnico; condición
 *  humana grande; footer con cooldown / último disparo / acciones. */
export function RuleCard({
  rule,
  onToggle,
  onDelete,
  isPending,
}: RuleCardProps) {
  const [confirmOpen, setConfirmOpen] = useState(false)
  const condition = summarizeAlertConditions(
    rule.spec.conditions,
    rule.spec.logic,
  )
  const cooldown = formatCooldown(rule.cooldown_s)
  const lastFired = rule.last_fired_at
    ? formatRelative(rule.last_fired_at)
    : "nunca disparada"

  return (
    <Card
      className={cn(
        "border-border bg-card/40 transition-opacity",
        !rule.enabled && "opacity-60",
      )}
    >
      <CardContent className="flex flex-col gap-3 p-4">
        {/* Row 1: nombre + status */}
        <div className="flex items-start justify-between gap-2">
          <h3 className="line-clamp-2 text-[14px] font-medium tracking-tight text-foreground">
            {rule.name}
          </h3>
          <Badge
            variant="outline"
            className={cn(
              "shrink-0 border-transparent font-mono text-[10px] uppercase tracking-[0.14em]",
            )}
            style={{
              color: rule.enabled ? "var(--long)" : "var(--fg-3)",
              backgroundColor: rule.enabled
                ? "color-mix(in oklch, var(--long) 12%, transparent)"
                : "color-mix(in oklch, var(--fg-3) 8%, transparent)",
              borderColor: rule.enabled
                ? "color-mix(in oklch, var(--long) 30%, transparent)"
                : "color-mix(in oklch, var(--fg-3) 25%, transparent)",
            }}
          >
            {rule.enabled ? "activa" : "inactiva"}
          </Badge>
        </div>

        {/* Row 2: eyebrow técnico */}
        <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--fg-3)]">
          {rule.spec.symbol} · {rule.spec.timeframe} · cierre de vela
        </span>

        {/* Row 3: condición humana */}
        <p className="text-[13px] leading-relaxed text-foreground/85">
          {condition}
        </p>

        {/* Row 4: footer meta + acciones */}
        <div className="mt-1 flex items-center justify-between gap-2 border-t border-[color:var(--line-soft)] pt-2.5">
          <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--fg-3)]">
            cooldown {cooldown} · {lastFired}
          </span>
          <div className="flex items-center gap-1">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-7"
              onClick={() => onToggle(rule)}
              disabled={isPending}
              aria-label={
                rule.enabled
                  ? `desactivar regla ${rule.name}`
                  : `activar regla ${rule.name}`
              }
              title={rule.enabled ? "desactivar" : "activar"}
            >
              <PowerIcon className="size-3.5" aria-hidden />
            </Button>
            <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
              <AlertDialogTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="size-7 text-[var(--fg-3)] hover:text-[var(--short)]"
                  aria-label={`borrar regla ${rule.name}`}
                  title="borrar"
                  disabled={isPending}
                >
                  <Trash2Icon className="size-3.5" aria-hidden />
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>¿Borrar regla?</AlertDialogTitle>
                  <AlertDialogDescription>
                    Vamos a desactivarla. La regla deja de disparar pero su
                    histórico de eventos se mantiene. Puedes recrearla desde
                    el chat cuando quieras.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancelar</AlertDialogCancel>
                  <AlertDialogAction
                    onClick={() => {
                      onDelete(rule)
                      setConfirmOpen(false)
                    }}
                  >
                    Borrar regla
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function formatRelative(iso: string): string {
  const dt = new Date(iso)
  const diffMs = Date.now() - dt.getTime()
  const min = Math.round(diffMs / 60_000)
  if (min < 1) return "disparada ahora"
  if (min < 60) return `disparada hace ${min}m`
  const h = Math.round(min / 60)
  if (h < 24) return `disparada hace ${h}h`
  const d = Math.round(h / 24)
  return `disparada hace ${d}d`
}
