"use client"

import { useQuery } from "@tanstack/react-query"
import Link from "next/link"

import { AlertsHero } from "@/components/alerts/AlertsHero"
import { EventsHistory } from "@/components/alerts/EventsHistory"
import { RulesTab } from "@/components/alerts/RulesTab"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs"
import {
  fetchAlertEvents,
  fetchAlerts,
  type AlertEventDTO,
  type AlertRuleDTO,
} from "@/lib/core/api"

export default function AlertsPage() {
  const rulesQuery = useQuery<AlertRuleDTO[]>({
    queryKey: ["alerts"],
    queryFn: ({ signal }) => fetchAlerts({ signal }),
    staleTime: 30_000,
  })
  const eventsQuery = useQuery<AlertEventDTO[]>({
    queryKey: ["alert-events", { limit: 200 }],
    queryFn: ({ signal }) => fetchAlertEvents({ limit: 200, signal }),
    refetchInterval: 60_000,
    staleTime: 30_000,
  })

  const rules = rulesQuery.data ?? []
  const events = eventsQuery.data ?? []
  const heroLoading = rulesQuery.isLoading || eventsQuery.isLoading

  return (
    <main className="flex min-h-0 flex-1 flex-col overflow-y-auto overflow-x-hidden">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6">
        <header className="flex flex-col gap-1">
          <h1 className="text-xl font-semibold tracking-tight text-foreground">
            Alertas
          </h1>
          <p className="text-[13px] text-muted-foreground">
            Las reglas disparan cuando una vela cierra cumpliendo tus
            condiciones; los eventos llegan en vivo a la campana. Crea
            nuevas desde el{" "}
            <Link
              href="/"
              className="text-foreground underline-offset-4 hover:underline"
            >
              chat
            </Link>{" "}
            (ej. &ldquo;alértame cuando BTCUSDT 4h cierre con
            RSI(14)&le;30&rdquo;).
          </p>
        </header>

        {heroLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : (
          <AlertsHero rules={rules} events={events} />
        )}

        <Tabs defaultValue="rules" className="w-full">
          <TabsList>
            <TabsTrigger value="rules">
              Reglas
              <span className="ml-1.5 font-mono text-[10px] tabular-nums text-[var(--fg-3)]">
                {rules.length}
              </span>
            </TabsTrigger>
            <TabsTrigger value="history">
              Histórico
              <span className="ml-1.5 font-mono text-[10px] tabular-nums text-[var(--fg-3)]">
                {events.length}
              </span>
            </TabsTrigger>
          </TabsList>
          <TabsContent value="rules" className="mt-4">
            {rulesQuery.isLoading ? (
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3">
                <Skeleton className="h-44 w-full" />
                <Skeleton className="h-44 w-full" />
                <Skeleton className="h-44 w-full" />
              </div>
            ) : rulesQuery.error ? (
              <p className="text-[13px] text-destructive">
                Error: {(rulesQuery.error as Error).message}
              </p>
            ) : (
              <RulesTab rules={rules} />
            )}
          </TabsContent>
          <TabsContent value="history" className="mt-4">
            <EventsHistory />
          </TabsContent>
        </Tabs>
      </div>
    </main>
  )
}
