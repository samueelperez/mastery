"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CheckIcon, MinusIcon, PowerIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Card } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { formatShortDateTime, summarizeAlertConditions } from "@/lib/format"
import { cn } from "@/lib/utils"
import {
  type AlertRuleDTO,
  fetchAlerts,
  patchAlert,
} from "@/lib/api"

export function AlertList() {
  const qc = useQueryClient()
  const { data, isLoading, error } = useQuery({
    queryKey: ["alerts"],
    queryFn: ({ signal }) => fetchAlerts({ signal }),
  })
  const toggle = useMutation({
    mutationFn: (rule: AlertRuleDTO) =>
      patchAlert(rule.id, { enabled: !rule.enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts"] }),
  })

  if (isLoading && !data) {
    return (
      <div className="flex flex-col gap-2">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
    )
  }
  if (error) return <p className="text-xs text-destructive">{String(error)}</p>
  if (!data || data.length === 0) {
    return (
      <Card className="border-dashed border-border bg-card/20 p-6 text-center">
        <p className="font-mono text-xs uppercase tracking-widest text-muted-foreground">
          aún no hay alertas
        </p>
        <p className="mt-2 text-xs text-muted-foreground">
          Pídele al copiloto:{" "}
          <span className="font-mono text-foreground">
            &ldquo;alértame cuando BTCUSDT 4h cierre con RSI(14)&le;30&rdquo;
          </span>
        </p>
      </Card>
    )
  }

  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <table className="w-full min-w-[40rem] text-xs">
        <thead className="bg-card text-[11px] uppercase tracking-widest text-muted-foreground">
          <tr>
            <th className="px-3 py-2 pointer-coarse:py-4 text-left font-medium">nombre</th>
            <th className="px-3 py-2 pointer-coarse:py-4 text-left font-medium">símbolo</th>
            <th className="px-3 py-2 pointer-coarse:py-4 text-left font-medium">tf</th>
            <th className="px-3 py-2 pointer-coarse:py-4 text-left font-medium">condición</th>
            <th className="px-3 py-2 pointer-coarse:py-4 text-right font-medium">último disparo</th>
            <th className="px-3 py-2 pointer-coarse:py-4 text-left font-medium">estado</th>
            <th className="px-3 py-2 pointer-coarse:py-4 text-right font-medium" />
          </tr>
        </thead>
        <tbody className="divide-y divide-border/30">
          {data.map((r) => (
            <tr
              key={r.id}
              className={cn(
                "transition-colors duration-150 ease-out hover:bg-accent/10",
                !r.enabled && "opacity-60",
              )}
            >
              <td className="px-3 py-2 pointer-coarse:py-4 font-mono text-foreground">
                {r.name}
              </td>
              <td className="px-3 py-2 pointer-coarse:py-4">{r.spec.symbol}</td>
              <td className="px-3 py-2 pointer-coarse:py-4">{r.spec.timeframe}</td>
              <td className="px-3 py-2 pointer-coarse:py-4 font-mono text-[11px] text-muted-foreground">
                {summarizeAlertConditions(r.spec.conditions, r.spec.logic)}
              </td>
              <td className="px-3 py-2 pointer-coarse:py-4 text-right font-mono tabular-nums">
                {r.last_fired_at ? formatShortDateTime(r.last_fired_at) : "—"}
              </td>
              <td className="px-3 py-2 pointer-coarse:py-4">
                {r.enabled ? (
                  <Badge variant="secondary" className="gap-1">
                    <CheckIcon className="size-3" />
                    activa
                  </Badge>
                ) : (
                  <Badge variant="outline" className="gap-1 text-muted-foreground">
                    <MinusIcon className="size-3" />
                    inactiva
                  </Badge>
                )}
              </td>
              <td className="px-3 py-2 pointer-coarse:py-4 text-right">
                <button
                  type="button"
                  onClick={() => toggle.mutate(r)}
                  className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent/20 hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-1"
                  aria-label={r.enabled ? "desactivar alerta" : "activar alerta"}
                >
                  <PowerIcon className="size-3.5" />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
