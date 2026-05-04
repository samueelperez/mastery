"use client"

import { ArrowUpRightIcon, BellIcon } from "lucide-react"
import Link from "next/link"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader } from "@/components/ui/card"

interface AlertCreatedToolOutput {
  alert_id: string
  name: string
  spec: {
    symbol: string
    timeframe: string
    conditions: { left: string; op: string; right: number | string }[]
    logic: "all" | "any"
  }
  cooldown_s: number
}

interface AlertCreatedCardProps {
  output: AlertCreatedToolOutput
}

function summarizeConditions(
  conds: { left: string; op: string; right: number | string }[],
  logic: "all" | "any",
): string {
  const parts = conds.map((c) => `${c.left} ${c.op} ${c.right}`)
  if (parts.length === 1) return parts[0]
  return parts.join(logic === "all" ? " AND " : " OR ")
}

export function AlertCreatedCard({ output: o }: AlertCreatedCardProps) {
  return (
    <Card className="border-border bg-card">
      <CardHeader className="space-y-1 pb-3">
        <div className="flex items-baseline justify-between gap-3">
          <span className="flex items-center gap-2 font-mono text-sm tracking-tight text-foreground">
            <BellIcon className="size-3.5 text-primary" aria-hidden />
            alert created
          </span>
          <Badge variant="secondary" className="font-mono text-[10px]">
            cooldown {o.cooldown_s}s
          </Badge>
        </div>
        <p className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
          {o.spec.symbol} · {o.spec.timeframe} · alert_id{" "}
          <span className="text-foreground">{o.alert_id.slice(0, 8)}</span>
        </p>
      </CardHeader>
      <CardContent className="space-y-3 pb-4">
        <div>
          <p className="mb-1 font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
            name
          </p>
          <p className="font-mono text-sm text-foreground">{o.name}</p>
        </div>
        <div>
          <p className="mb-1 font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
            condition ({o.spec.logic})
          </p>
          <p className="font-mono text-xs text-muted-foreground">
            {summarizeConditions(o.spec.conditions, o.spec.logic)}
          </p>
        </div>
        <Link
          href="/alerts"
          className="group flex items-center gap-1 font-mono text-[11px] uppercase tracking-widest text-muted-foreground hover:text-foreground"
        >
          manage alerts
          <ArrowUpRightIcon className="size-3 transition-transform group-hover:translate-x-0.5" />
        </Link>
      </CardContent>
    </Card>
  )
}

export type { AlertCreatedToolOutput }
