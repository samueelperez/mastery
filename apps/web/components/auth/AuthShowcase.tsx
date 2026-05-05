"use client"

import { BrandMark } from "@/components/nav/BrandMark"
import { cn } from "@/lib/utils"

import { LivePulse } from "./LivePulse"

/** Left-column "pitch" para /auth/* en desktop.
 *
 * Estructura:
 *   - header: brand mark + wordmark + status pill
 *   - eyebrow violet con barra
 *   - título mono grande con cursor blink amber
 *   - sub-copy sans
 *   - live-bullets (LivePulse)
 *   - terminal box decorativa
 *   - footer con tagline amber
 *
 * Sin Lightweight Charts: el live-feel se da por el LivePulse y la terminal
 * box. Eso evita el coste del chart en una superficie que el usuario
 * abandona en segundos. */
export function AuthShowcase({ className }: { className?: string }) {
  return (
    <aside
      className={cn(
        "relative flex flex-col gap-7 overflow-hidden border-r border-border/50 px-10 py-10 xl:px-14 xl:py-14",
        className,
      )}
    >
      {/* Backdrop: dot grid + dos gradientes radiales */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 grid-bg opacity-60"
        style={{
          maskImage:
            "radial-gradient(ellipse at 30% 30%, black 20%, transparent 70%)",
          WebkitMaskImage:
            "radial-gradient(ellipse at 30% 30%, black 20%, transparent 70%)",
        }}
      />
      <div
        aria-hidden
        className="pointer-events-none absolute -left-32 -top-32 size-[28rem] rounded-full"
        style={{
          background:
            "radial-gradient(circle, oklch(0.68 0.18 290 / 0.20) 0%, transparent 60%)",
        }}
      />
      <div
        aria-hidden
        className="pointer-events-none absolute -bottom-40 -right-32 size-[28rem] rounded-full"
        style={{
          background:
            "radial-gradient(circle, oklch(0.78 0.16 75 / 0.10) 0%, transparent 60%)",
        }}
      />

      <header className="relative z-10 flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <BrandMark size={22} />
          <span className="font-sans text-[16px] tracking-tight">
            <span className="font-semibold text-foreground">Mastery</span>{" "}
            <span className="font-medium text-[var(--fg-2)]">Trader</span>
          </span>
        </div>
        <span className="pill">
          <span className="dot dot-live" aria-hidden />
          <span>sistemas operativos</span>
          <span className="text-[var(--fg-4)]">·</span>
          <span>v0.5.0</span>
        </span>
      </header>

      <div className="relative z-10 flex items-center gap-3">
        <span className="h-px w-8 bg-[var(--violet)]" aria-hidden />
        <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--violet)]">
          acceso · sesión segura
        </span>
      </div>

      <h1 className="relative z-10 font-mono text-[34px] leading-[1.15] tracking-tight text-foreground xl:text-[38px]">
        Tu copiloto
        <br />
        para leer el{" "}
        <span className="text-[var(--amber)]">mercado</span>,
        <br />
        no para predecirlo
        <span
          aria-hidden
          className="ml-1 inline-block h-[0.9em] w-[0.55em] translate-y-[0.05em] bg-[var(--amber)] align-middle [animation:cursor-blink_1s_steps(2)_infinite] motion-reduce:animate-none"
        />
      </h1>

      <p className="relative z-10 max-w-md font-sans text-[14px] leading-relaxed text-[var(--fg-1)]">
        Cada cifra cita la herramienta determinista que la produjo. DSR +
        walk-forward + CPCV antes de paper. Diario con embeddings. Alertas que
        disparan a cierre de vela.
      </p>

      <div className="relative z-10">
        <LivePulse />
      </div>

      {/* Terminal box decorativa — mock CLI */}
      <pre
        aria-hidden
        className="relative z-10 m-0 overflow-hidden rounded-md border border-border/50 bg-[var(--bg-inset)]/80 px-4 py-3 font-mono text-[11px] leading-relaxed text-[var(--fg-2)]"
      >
        <span className="text-[var(--violet)]">$</span> connect binance.usdt-m
        {"\n"}
        <span className="text-[var(--long)]">›</span> handshake ok ·{" "}
        <span className="tabular-nums">latency 42ms</span>
        {"\n"}
        <span className="text-[var(--violet)]">$</span> copilot.boot --mode
        interpreter
        {"\n"}
        <span className="text-[var(--long)]">›</span> ready · awaiting auth
        <span
          className="ml-0.5 inline-block h-3 w-1.5 -translate-y-0.5 bg-[var(--fg-2)] [animation:cursor-blink_1s_steps(2)_infinite] motion-reduce:animate-none"
          aria-hidden
        />
      </pre>

      <footer className="relative z-10 mt-auto flex flex-col gap-1 border-t border-border/40 pt-5">
        <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--fg-3)]">
          intérprete y orquestador
        </span>
        <span className="font-mono text-[12px] tracking-tight text-[var(--amber)]">
          nunca un oráculo
        </span>
      </footer>
    </aside>
  )
}
