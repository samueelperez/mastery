"use client"

import { ActivityIcon, BellOffIcon, BellRingIcon } from "lucide-react"
import { useMemo } from "react"

import { Card, CardContent } from "@/components/ui/card"
import type { AlertEventDTO, AlertRuleDTO } from "@/lib/core/api"
import { cn } from "@/lib/core/utils"

interface AlertsHeroProps {
  rules: AlertRuleDTO[]
  events: AlertEventDTO[]
}

/** 3 KPIs simples del estado del sistema de alertas:
 *  - activas: reglas con `enabled=true`.
 *  - inactivas: reglas con `enabled=false`.
 *  - disparadas en últimas 24h: count de eventos `fired_at >= now - 24h`.
 *
 *  Si no hay reglas, hero pasa a empty state con CTA al chat. */
export function AlertsHero({ rules, events }: AlertsHeroProps) {
  const stats = useMemo(() => {
    const active = rules.filter((r) => r.enabled).length
    const inactive = rules.length - active
    const cutoff24h = Date.now() - 24 * 60 * 60 * 1000
    const cutoff7d = Date.now() - 7 * 24 * 60 * 60 * 1000
    const cutoff48h = Date.now() - 48 * 60 * 60 * 1000
    let fired24h = 0
    let firedYesterday = 0
    let fired7d = 0
    for (const e of events) {
      const ms = Date.parse(e.fired_at)
      if (!Number.isFinite(ms)) continue
      if (ms >= cutoff24h) fired24h += 1
      else if (ms >= cutoff48h) firedYesterday += 1
      if (ms >= cutoff7d) fired7d += 1
    }
    return { active, inactive, fired24h, firedYesterday, fired7d }
  }, [rules, events])

  if (rules.length === 0) {
    return (
      <Card className="border-dashed border-border bg-card/20">
        <CardContent className="flex flex-col items-start gap-1.5 p-6">
          <span className="eyebrow flex items-center gap-2">
            <BellOffIcon
              className="size-3.5 text-[var(--fg-3)]"
              aria-hidden
            />
            sin reglas
          </span>
          <p className="text-[14px] text-foreground">
            Aún no tienes reglas de alerta.
          </p>
          <p className="text-[13px] text-muted-foreground">
            Pídele al copiloto:{" "}
            <span className="font-mono text-foreground">
              &ldquo;alértame cuando BTCUSDT 4h cierre con RSI(14)≤30&rdquo;
            </span>
            .
          </p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card className="border-border bg-card/40">
      <CardContent className="grid grid-cols-1 gap-x-6 gap-y-4 p-5 sm:grid-cols-3">
        <Stat
          icon={BellRingIcon}
          label="activas"
          value={stats.active}
          tone={stats.active > 0 ? "var(--long)" : "var(--fg-2)"}
          caption="vigilando ahora"
        />
        <Stat
          icon={BellOffIcon}
          label="inactivas"
          value={stats.inactive}
          tone="var(--fg-2)"
          caption={stats.inactive === 1 ? "pausada" : "pausadas"}
        />
        <Stat
          icon={ActivityIcon}
          label="disparadas 24h"
          value={stats.fired24h}
          tone={
            stats.fired24h > 0
              ? "var(--long)"
              : stats.active > 0
                ? "var(--amber)"
                : "var(--fg-2)"
          }
          caption={`ayer ${stats.firedYesterday} · esta sem ${stats.fired7d}`}
        />
      </CardContent>
    </Card>
  )
}

interface StatProps {
  icon: React.ComponentType<{ className?: string }>
  label: string
  value: number
  tone: string
  caption: string
}

function Stat({ icon: Icon, label, value, tone, caption }: StatProps) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <Icon className="size-3.5 text-[var(--fg-3)]" aria-hidden />
        <span className="eyebrow">{label}</span>
      </div>
      <span
        className={cn(
          "font-mono text-3xl font-medium tabular-nums leading-none tracking-tight",
        )}
        style={{ color: tone }}
      >
        {value}
      </span>
      <span className="text-[12px] text-muted-foreground">{caption}</span>
    </div>
  )
}
