"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"

import { cn } from "@/lib/utils"

interface SubnavItem {
  href: string
  label: string
  exact?: boolean
}

const ITEMS: SubnavItem[] = [
  { href: "/research", label: "overview", exact: true },
  { href: "/research/backtests", label: "backtests" },
  { href: "/research/journal", label: "journal" },
]

/** Sub-nav rendered under the global nav, only on /research/* routes. */
export function ResearchSubnav() {
  const pathname = usePathname()
  return (
    <div className="sticky top-14 z-20 border-b border-border bg-background/95 backdrop-blur">
      <nav
        aria-label="research sections"
        className="-mx-1 flex items-center gap-1 overflow-x-auto px-4 py-1.5 sm:px-6"
      >
        {ITEMS.map(({ href, label, exact }) => {
          const active = exact ? pathname === href : pathname.startsWith(href)
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "rounded-md px-2.5 py-1 pointer-coarse:py-2 font-mono text-[11px] uppercase tracking-widest transition-colors duration-150 ease-out",
                "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2",
                active
                  ? "bg-accent/30 text-foreground"
                  : "text-muted-foreground hover:bg-accent/15 hover:text-foreground",
              )}
            >
              {label}
            </Link>
          )
        })}
      </nav>
    </div>
  )
}
