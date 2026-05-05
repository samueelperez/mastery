"use client"

import { Suspense } from "react"

import { LiveChart } from "@/components/chart/LiveChart"
import { CopilotChat } from "@/components/chat/CopilotChat"
import { SymbolSidebar } from "@/components/dashboard/SymbolSidebar"
import { useActiveSymbol } from "@/lib/store/active-symbol"
import { useActiveSymbolUrlSync } from "@/lib/store/use-symbol-url-sync"

export default function Page() {
  return (
    <Suspense>
      <Dashboard />
    </Suspense>
  )
}

function Dashboard() {
  useActiveSymbolUrlSync()
  const { symbol, timeframe } = useActiveSymbol()

  return (
    <main className="flex min-h-0 flex-1 flex-col gap-4 p-4 sm:p-6">
      <section
        className="
          grid min-h-0 flex-1 grid-cols-1 gap-4
          lg:grid-cols-[200px_minmax(0,1fr)_28rem]
        "
      >
        <SymbolSidebar />
        <div className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-border bg-card p-3">
          <LiveChart symbol={symbol} timeframe={timeframe} />
        </div>
        <CopilotChat className="h-full" />
      </section>
    </main>
  )
}
