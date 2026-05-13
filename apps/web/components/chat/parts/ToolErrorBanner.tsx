"use client"

import { AlertTriangleIcon } from "lucide-react"

import { toolLabel } from "@/lib/chat/tool-labels"
import { cn } from "@/lib/core/utils"

interface ToolErrorBannerProps {
  toolName: string
  errorText?: string
}

/** Banner inline mostrado cuando un tool falla (state === "output-error").
 *  Variante destructive del ToolRunningBanner, sin spinner. */
export function ToolErrorBanner({ toolName, errorText }: ToolErrorBannerProps) {
  const pretty = toolLabel(toolName)
  return (
    <div
      role="alert"
      className={cn(
        "flex items-start gap-2.5 rounded-md border border-border bg-[var(--short-bg)]/30",
        "border-l-2 border-l-[var(--short)]",
        "px-3 py-2",
      )}
    >
      <AlertTriangleIcon
        className="mt-0.5 size-3.5 shrink-0 text-[var(--short)]"
        aria-hidden
      />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <p className="font-mono text-[12px] text-foreground">
          Error en <strong className="text-[var(--short)]">{pretty}</strong>
        </p>
        {errorText ? (
          <p className="font-mono text-[10.5px] leading-relaxed text-[var(--fg-2)]">
            {errorText}
          </p>
        ) : null}
      </div>
    </div>
  )
}
