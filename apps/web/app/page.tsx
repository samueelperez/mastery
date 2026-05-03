import { LiveBtcChart } from "@/components/chart/LiveBtcChart"

export default function Page() {
  return (
    <main className="flex min-h-svh flex-col gap-4 p-6">
      <header className="flex items-baseline justify-between border-b border-border/40 pb-3">
        <div>
          <h1 className="font-mono text-sm tracking-tight text-foreground">trading-copilot</h1>
          <p className="text-xs text-muted-foreground">
            Phase 0 — interpreter and orchestrator, never an oracle.
          </p>
        </div>
        <div className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
          Binance USDT-M · MAINNET-RO
        </div>
      </header>

      <section className="grid h-[calc(100svh-9rem)] grid-cols-1 gap-4">
        <div className="overflow-hidden rounded-lg border border-border/40 bg-card/40 p-3">
          <LiveBtcChart symbol="BTCUSDT" timeframe="1h" className="h-[calc(100%-1.75rem)] w-full" />
        </div>
      </section>
    </main>
  )
}
