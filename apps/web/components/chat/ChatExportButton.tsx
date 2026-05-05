"use client"

import { ArrowDownToLineIcon } from "lucide-react"
import type { UIMessage } from "ai"
import { useEffect, useState } from "react"

import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { authClient } from "@/lib/auth/auth-client"
import {
  downloadMarkdown,
  exportChatToMarkdown,
} from "@/lib/chat-md-export"
import { cn } from "@/lib/utils"

const ALLOWED_EMAIL = "samuelpt@lienzzo.com"

interface ChatExportButtonProps {
  messages: UIMessage[]
  activeSymbol?: string
  activeTimeframe?: string
  className?: string
}

export function ChatExportButton({
  messages,
  activeSymbol,
  activeTimeframe,
  className,
}: ChatExportButtonProps) {
  // authClient.useSession() devuelve `null` en SSR (sin cookies del cliente)
  // y el email real en cliente — diferir el render del botón hasta tras
  // hydration evita el mismatch SSR/CSR. Mientras tanto reservamos el mismo
  // footprint (size-7) con un placeholder byte-identical entre server y
  // primer render cliente.
  const [mounted, setMounted] = useState(false)
  useEffect(() => setMounted(true), [])

  const { data, isPending } = authClient.useSession()

  if (!mounted || isPending) {
    return <span className={cn("size-7", className)} aria-hidden />
  }

  const email = data?.user?.email
  if (!email || email.toLowerCase() !== ALLOWED_EMAIL.toLowerCase()) {
    return null
  }

  const disabled = messages.length === 0

  const handleClick = () => {
    if (disabled) return
    const md = exportChatToMarkdown(messages, {
      activeSymbol,
      activeTimeframe,
    })
    const ts = new Date()
      .toISOString()
      .replace(/[:.]/g, "-")
      .replace(/T/, "_")
      .slice(0, 19)
    downloadMarkdown(`mastery-chat-${ts}.md`, md)
  }

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          onClick={handleClick}
          disabled={disabled}
          aria-label="Descargar chat como Markdown"
          className={cn(
            "flex size-7 items-center justify-center rounded-sm transition-colors",
            "text-[var(--fg-2)] hover:bg-[var(--bg-2)]/60 hover:text-foreground",
            "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-[-2px]",
            "disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-[var(--fg-2)]",
            className,
          )}
        >
          <ArrowDownToLineIcon className="size-4" aria-hidden />
        </button>
      </TooltipTrigger>
      <TooltipContent side="bottom" sideOffset={4}>
        <span className="font-mono text-[10px] uppercase tracking-[0.12em]">
          descargar chat .md
        </span>
      </TooltipContent>
    </Tooltip>
  )
}
