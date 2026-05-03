"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"

import { cn } from "@/lib/utils"

const ITEMS = [
  { href: "/research", label: "overview", exact: true },
  { href: "/research/backtests", label: "backtests" },
  { href: "/research/journal", label: "journal" },
]

export function ResearchNav() {
  const pathname = usePathname()
  return (
    <nav className="flex flex-col gap-1">
      {ITEMS.map(({ href, label, exact }) => {
        const active = exact ? pathname === href : pathname.startsWith(href)
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "rounded-md px-2.5 py-1.5 font-mono text-xs uppercase tracking-wide transition-colors duration-150 ease-out",
              active
                ? "bg-accent/30 text-foreground"
                : "text-muted-foreground hover:bg-accent/20 hover:text-foreground",
            )}
          >
            {label}
          </Link>
        )
      })}
    </nav>
  )
}
