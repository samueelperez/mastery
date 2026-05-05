"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useMemo, useState } from "react"

import { RuleCard } from "@/components/alerts/RuleCard"
import { Card, CardContent } from "@/components/ui/card"
import {
  ToggleGroup,
  ToggleGroupItem,
} from "@/components/ui/toggle-group"
import {
  deleteAlert,
  patchAlert,
  type AlertRuleDTO,
} from "@/lib/api"

type StatusFilter = "all" | "enabled" | "disabled"

interface RulesTabProps {
  rules: AlertRuleDTO[]
}

const STATUS_LABEL: Record<StatusFilter, string> = {
  all: "Todas",
  enabled: "Activas",
  disabled: "Inactivas",
}

export function RulesTab({ rules }: RulesTabProps) {
  const qc = useQueryClient()
  const [filter, setFilter] = useState<StatusFilter>("all")

  const toggle = useMutation({
    mutationFn: (rule: AlertRuleDTO) =>
      patchAlert(rule.id, { enabled: !rule.enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts"] }),
  })
  const remove = useMutation({
    mutationFn: (rule: AlertRuleDTO) => deleteAlert(rule.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts"] }),
  })

  const counts = useMemo(() => {
    const enabled = rules.filter((r) => r.enabled).length
    return {
      all: rules.length,
      enabled,
      disabled: rules.length - enabled,
    }
  }, [rules])

  const visible = useMemo(() => {
    if (filter === "all") return rules
    if (filter === "enabled") return rules.filter((r) => r.enabled)
    return rules.filter((r) => !r.enabled)
  }, [rules, filter])

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--fg-3)]">
          mostrar
        </span>
        <ToggleGroup
          type="single"
          value={filter}
          onValueChange={(v) => v && setFilter(v as StatusFilter)}
          variant="outline"
          size="sm"
          spacing={1}
        >
          {(["all", "enabled", "disabled"] as StatusFilter[]).map((k) => (
            <ToggleGroupItem
              key={k}
              value={k}
              className="text-[12px] font-medium"
            >
              {STATUS_LABEL[k]}
              <span className="ml-1.5 font-mono text-[10px] tabular-nums text-[var(--fg-3)]">
                {counts[k]}
              </span>
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </div>

      {visible.length === 0 ? (
        <Card className="border-dashed border-border bg-card/20">
          <CardContent className="flex flex-col items-center gap-1 p-6 text-center">
            <p className="text-[13px] text-foreground">
              {filter === "all"
                ? "No hay reglas todavía."
                : filter === "enabled"
                  ? "No tienes reglas activas."
                  : "Todas tus reglas están activas."}
            </p>
            <p className="text-[12px] text-muted-foreground">
              {filter === "disabled"
                ? "Si pausas alguna aparecerá aquí."
                : "Pídele al copiloto una alerta nueva desde el chat."}
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3">
          {visible.map((r) => (
            <RuleCard
              key={r.id}
              rule={r}
              onToggle={(rule) => toggle.mutate(rule)}
              onDelete={(rule) => remove.mutate(rule)}
              isPending={toggle.isPending || remove.isPending}
            />
          ))}
        </div>
      )}
    </div>
  )
}
