"use client"

import { LiveBtcChart } from "@/components/chart/LiveBtcChart"
import { cn } from "@/lib/utils"

import { LivePulse } from "./LivePulse"

/** Left-column hero for /auth/* on desktop. Only mounts when the layout's
 * `lg:` breakpoint is active — mobile gets a plain centered card.
 *
 * The chart isn't decoration: it's the strongest signal that this is a
 * live tool. Same `LiveBtcChart` the home page uses, no auth required
 * (the /ohlcv + /ws/market endpoints stay public). */
export function AuthShowcase({ className }: { className?: string }) {
  return (
    <aside
      className={cn(
        "relative flex flex-col gap-6 border-r border-border/50 p-8 xl:p-12",
        className,
      )}
    >
      <header className="flex flex-col gap-2.5">
        <div className="h-1 w-12 bg-primary" aria-hidden />
        <h1 className="font-mono text-2xl tracking-tight text-foreground">
          trading-copilot
        </h1>
        <p className="max-w-md font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
          intérprete y orquestador · nunca un oráculo
        </p>
      </header>

      {/* Live chart panel — fills the available vertical space */}
      <section
        aria-label="Gráfico BTCUSDT en vivo"
        className="flex min-h-0 flex-1 flex-col gap-2 rounded-xl border border-border/60 bg-card/40 p-3"
      >
        <div className="flex items-center justify-between px-1 pt-0.5">
          <span className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground">
            BTCUSDT · 1h · en vivo
          </span>
          <span className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
            <span
              className="size-1.5 rounded-full bg-success"
              aria-hidden
            />
            mainnet · ro
          </span>
        </div>
        <div className="min-h-[18rem] flex-1">
          <LiveBtcChart
            symbol="BTCUSDT"
            timeframe="1h"
            className="h-full w-full"
          />
        </div>
      </section>

      <LivePulse />

      {/* Mission strip */}
      <footer className="flex flex-col gap-2 border-t border-border/40 pt-5">
        <p className="font-mono text-[11px] uppercase tracking-widest text-primary">
          qué hay detrás de la puerta
        </p>
        <ul className="grid grid-cols-1 gap-2 text-sm leading-relaxed text-muted-foreground sm:grid-cols-2">
          <li className="flex gap-2">
            <span className="mt-1.5 size-1 shrink-0 rounded-full bg-primary" aria-hidden />
            <span>
              <span className="font-mono text-foreground">14 herramientas deterministas</span>
              {" "}— cada cifra cita la llamada que la produjo.
            </span>
          </li>
          <li className="flex gap-2">
            <span className="mt-1.5 size-1 shrink-0 rounded-full bg-primary" aria-hidden />
            <span>
              <span className="font-mono text-foreground">DSR + walk-forward + CPCV</span>
              {" "}antes de que una estrategia llegue a paper.
            </span>
          </li>
          <li className="flex gap-2">
            <span className="mt-1.5 size-1 shrink-0 rounded-full bg-primary" aria-hidden />
            <span>
              <span className="font-mono text-foreground">Diario con embeddings</span>
              {" "}— trades pasados recuperados por similitud de setup.
            </span>
          </li>
          <li className="flex gap-2">
            <span className="mt-1.5 size-1 shrink-0 rounded-full bg-primary" aria-hidden />
            <span>
              <span className="font-mono text-foreground">Detector de sesgos</span>
              {" "}+ alertas que disparan al cierre de vela, no antes.
            </span>
          </li>
        </ul>
      </footer>
    </aside>
  )
}
