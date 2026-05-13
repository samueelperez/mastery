"use client"

import { ChevronDownIcon, WrenchIcon } from "lucide-react"

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { cn } from "@/lib/core/utils"

interface MessageWorkbenchProps {
  /** Pasos del razonamiento — cada uno será un ToolRunningBanner o una card
   *  de output (Indicator/Confluence/Structure) o un `<ReasoningPart>`. */
  children: React.ReactNode
  /** Número de pasos para el header. */
  count: number
}

/** Agrupa los tool calls intermedios y reasoning blocks de un mensaje del
 *  agente en un Collapsible cerrado por defecto. La idea: cuando el agente
 *  emite una TradeIdea (la respuesta principal), el razonamiento técnico
 *  detrás (5–7 tools) queda detrás de un "ver razonamiento" en lugar de
 *  inundar el chat. */
export function MessageWorkbench({ children, count }: MessageWorkbenchProps) {
  return (
    <Collapsible
      className={cn(
        "group/workbench rounded-md border border-[color:var(--line-soft)]",
        "bg-[var(--bg-1)]/40",
      )}
    >
      <CollapsibleTrigger
        className={cn(
          "flex w-full items-center gap-2 px-3 py-2",
          "text-left transition-colors hover:bg-[var(--bg-2)]/50",
          "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-[-2px] rounded-md",
        )}
      >
        <WrenchIcon
          className="size-3 text-[var(--violet)] opacity-80"
          aria-hidden
        />
        <span className="font-mono text-[11px] uppercase tracking-[0.12em] text-[var(--fg-2)]">
          razonamiento técnico
        </span>
        <span className="font-mono text-[10px] tabular-nums text-[var(--fg-3)]">
          · {count} {count === 1 ? "paso" : "pasos"}
        </span>
        <ChevronDownIcon
          className={cn(
            "ml-auto size-3 text-[var(--fg-3)] transition-transform",
            "group-data-[state=open]/workbench:rotate-180",
          )}
          aria-hidden
        />
      </CollapsibleTrigger>
      <CollapsibleContent
        className={cn(
          "border-t border-[color:var(--line-soft)] p-3",
          "flex flex-col gap-3",
          "data-[state=closed]:animate-out data-[state=open]:animate-in",
          "data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
        )}
      >
        {children}
      </CollapsibleContent>
    </Collapsible>
  )
}
