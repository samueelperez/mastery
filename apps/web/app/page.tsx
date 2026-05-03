import Link from "next/link"

import { LiveBtcChart } from "@/components/chart/LiveBtcChart"
import { CopilotChat } from "@/components/chat/CopilotChat"

export default function Page() {
  return (
    <main className="flex min-h-svh flex-col gap-4 p-6">
      <header className="flex items-baseline justify-between border-b border-border pb-3">
        <div>
          <h1 className="font-mono text-sm tracking-tight text-foreground">
            trading-copilot
          </h1>
          <p className="text-xs text-muted-foreground">
            Phase 2 — chat-driven analysis + reproducible research.
          </p>
        </div>
        <div className="flex items-center gap-4">
          <Link
            href="/research"
            className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground hover:text-foreground"
          >
            research →
          </Link>
          <span className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
            Binance USDT-M · MAINNET-RO
          </span>
        </div>
      </header>

      <section className="grid h-[calc(100svh-9rem)] grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_28rem]">
        <div className="overflow-hidden rounded-lg border border-border bg-card p-3">
          <LiveBtcChart
            symbol="BTCUSDT"
            timeframe="1h"
            className="h-[calc(100%-1.75rem)] w-full"
          />
        </div>
        <CopilotChat className="h-full" />
      </section>
    </main>
  )
}
