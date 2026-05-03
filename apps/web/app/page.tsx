import { LiveBtcChart } from "@/components/chart/LiveBtcChart"
import { CopilotChat } from "@/components/chat/CopilotChat"

export default function Page() {
  return (
    <main className="flex min-h-[calc(100svh-3.5rem)] flex-col gap-4 p-4 sm:p-6">
      <section className="grid h-[calc(100svh-6rem)] grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_28rem]">
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
