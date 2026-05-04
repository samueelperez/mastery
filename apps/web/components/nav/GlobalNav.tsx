"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { BellRingIcon, FlaskConicalIcon, MessageSquareIcon } from "lucide-react"

import { cn } from "@/lib/utils"

import { AlertBell } from "./AlertBell"
import { ConnectionPill } from "./ConnectionPill"

interface NavItem {
  href: string
  label: string
  icon: typeof MessageSquareIcon
  exact?: boolean
}

const ITEMS: NavItem[] = [
  { href: "/", label: "chat", icon: MessageSquareIcon, exact: true },
  { href: "/research", label: "research", icon: FlaskConicalIcon },
  { href: "/alerts", label: "alerts", icon: BellRingIcon },
]

export function GlobalNav() {
  const pathname = usePathname()
  // Auth pages own the full screen — no top nav.
  if (pathname?.startsWith("/auth")) return null
  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-border bg-background/95 px-4 backdrop-blur sm:gap-4 sm:px-6">
      <Link
        href="/"
        className="font-mono text-sm tracking-tight text-foreground transition-colors duration-150 hover:text-primary"
      >
        trading-copilot
      </Link>
      <nav className="flex items-center gap-1" aria-label="primary">
        {ITEMS.map(({ href, label, icon: Icon, exact }) => {
          const active = exact ? pathname === href : pathname.startsWith(href)
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex h-8 items-center gap-1.5 rounded-md px-2 font-mono text-xs uppercase tracking-wide transition-colors duration-150 ease-out",
                "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2",
                active
                  ? "bg-accent/30 text-foreground"
                  : "text-muted-foreground hover:bg-accent/15 hover:text-foreground",
              )}
            >
              <Icon className="size-3.5" aria-hidden />
              <span className="hidden sm:inline">{label}</span>
            </Link>
          )
        })}
      </nav>
      <div className="ml-auto flex items-center gap-2 sm:gap-3">
        <AlertBell />
        <ConnectionPill />
      </div>
    </header>
  )
}
