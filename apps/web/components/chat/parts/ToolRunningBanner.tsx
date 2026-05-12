"use client"

import {
  ActivityIcon,
  BarChart3Icon,
  BeakerIcon,
  BellIcon,
  BookmarkIcon,
  CandlestickChartIcon,
  LayersIcon,
  SearchIcon,
  TrendingUpIcon,
  WrenchIcon,
} from "lucide-react"
import type { ReactNode } from "react"

import { Spinner } from "@/components/ui/spinner"
import { toolLabel } from "@/lib/chat/tool-labels"
import { cn } from "@/lib/core/utils"

interface ToolRunningBannerProps {
  toolName: string
  input?: unknown
}

/** Banner inline mostrado mientras un tool está en `input-streaming` o
 *  `input-available`. Reemplaza al `ToolPart` genérico (Collapsible cerrado)
 *  con narración legible en castellano. Cuando llega `output-available`,
 *  el routing de CopilotChat sustituye este banner por la card específica.
 */
export function ToolRunningBanner({ toolName, input }: ToolRunningBannerProps) {
  const meta = describeTool(toolName, input)
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "flex items-center gap-2.5 rounded-md border border-border bg-[var(--bg-2)]/40",
        "border-l-2 border-l-[var(--violet)]",
        "px-3 py-2",
      )}
    >
      <Spinner className="size-3.5 text-[var(--violet)]" aria-hidden />
      <span
        aria-hidden
        className="grid size-5 place-items-center text-[var(--fg-2)]"
      >
        {meta.icon}
      </span>
      <p className="font-mono text-[12px] text-[var(--fg-1)]">{meta.text}</p>
    </div>
  )
}

interface ToolDescription {
  icon: ReactNode
  text: ReactNode
}

interface CommonInput {
  symbol?: unknown
  timeframe?: unknown
  timeframes?: unknown
  strategy_id?: unknown
  name?: unknown
  indicators?: unknown
  pivot_strength?: unknown
  conditions?: unknown
}

function describeTool(toolName: string, rawInput: unknown): ToolDescription {
  const input = (rawInput && typeof rawInput === "object" ? rawInput : {}) as CommonInput
  const symbol = typeof input.symbol === "string" ? input.symbol.toUpperCase() : null
  const tf = typeof input.timeframe === "string" ? input.timeframe : null
  const symTf = symbol && tf ? `${symbol} ${tf}` : (symbol ?? tf ?? null)

  switch (toolName) {
    case "get_ohlcv":
      return {
        icon: <CandlestickChartIcon className="size-3" />,
        text: (
          <>
            Cargando velas de <strong className="text-foreground">{symTf ?? "el símbolo"}</strong>…
          </>
        ),
      }
    case "get_indicators": {
      const list = Array.isArray(input.indicators)
        ? (input.indicators as Array<{ name?: unknown; length?: unknown }>)
            .map((s) => {
              const name = typeof s?.name === "string" ? s.name : null
              const len = typeof s?.length === "number" ? s.length : null
              return name ? (len ? `${name.toUpperCase()} ${len}` : name.toUpperCase()) : null
            })
            .filter(Boolean)
            .slice(0, 4)
            .join(", ")
        : ""
      return {
        icon: <ActivityIcon className="size-3" />,
        text: (
          <>
            Computando{" "}
            <strong className="text-foreground">{list || "indicadores"}</strong>{" "}
            en <strong className="text-foreground">{symTf ?? "el símbolo"}</strong>…
          </>
        ),
      }
    }
    case "get_multi_tf_confluence": {
      const tfs = Array.isArray(input.timeframes)
        ? (input.timeframes as unknown[]).filter((t) => typeof t === "string").length
        : 4
      return {
        icon: <LayersIcon className="size-3" />,
        text: (
          <>
            Evaluando confluencia en{" "}
            <strong className="text-foreground">{symbol ?? "el símbolo"}</strong>{" "}
            ({tfs} {tfs === 1 ? "timeframe" : "timeframes"})…
          </>
        ),
      }
    }
    case "get_market_structure":
      return {
        icon: <TrendingUpIcon className="size-3" />,
        text: (
          <>
            Analizando estructura en{" "}
            <strong className="text-foreground">{symTf ?? "el símbolo"}</strong>
            {typeof input.pivot_strength === "number"
              ? ` (pivots fractal-${input.pivot_strength})`
              : ""}
            …
          </>
        ),
      }
    case "run_backtest": {
      const strat = typeof input.strategy_id === "string" ? input.strategy_id : null
      return {
        icon: <BeakerIcon className="size-3" />,
        text: (
          <>
            Ejecutando backtest{" "}
            <strong className="text-foreground">{strat ?? "estrategia"}</strong>
            {symTf ? (
              <>
                {" en "}
                <strong className="text-foreground">{symTf}</strong>
              </>
            ) : null}
            …
          </>
        ),
      }
    }
    case "create_alert": {
      const name = typeof input.name === "string" ? input.name : null
      return {
        icon: <BellIcon className="size-3" />,
        text: (
          <>
            Creando alerta{" "}
            {name ? (
              <strong className="text-foreground">«{name}»</strong>
            ) : (
              <span>nueva</span>
            )}
            {symTf ? (
              <>
                {" en "}
                <strong className="text-foreground">{symTf}</strong>
              </>
            ) : null}
            …
          </>
        ),
      }
    }
    case "log_trade":
      return {
        icon: <BookmarkIcon className="size-3" />,
        text: <>Registrando operación en el diario…</>,
      }
    case "journal_query":
    case "get_similar_past_trades":
      return {
        icon: <SearchIcon className="size-3" />,
        text: <>Buscando trades similares en el diario…</>,
      }
    case "get_strategy_metrics":
      return {
        icon: <BarChart3Icon className="size-3" />,
        text: <>Consultando métricas de la estrategia…</>,
      }
    default:
      return {
        icon: <WrenchIcon className="size-3" />,
        text: (
          <>
            Ejecutando{" "}
            <strong className="text-foreground">{toolLabel(toolName)}</strong>
            …
          </>
        ),
      }
  }
}
