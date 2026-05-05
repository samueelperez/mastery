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
  { href: "/research", label: "Resumen", exact: true },
  { href: "/research/strategies", label: "Estrategias" },
  { href: "/research/backtests", label: "Backtests" },
]

/** Sub-nav rendered under the global nav, only on /research/* routes.
 *
 *  Diseño post-tipografía: sans 14px medium en Title Case (en lugar del
 *  mono uppercase 11px previo). Active state: pill `bg-[var(--bg-2)]`
 *  rounded-md (sustituye el underline 2px violet). Sticky `top-14` para
 *  alinear con el GlobalNav h-14 nuevo. */
export function ResearchSubnav() {
  const pathname = usePathname()
  return (
    <div className="sticky top-14 z-20 border-b border-border bg-background/95 backdrop-blur">
      <nav
        aria-label="secciones de investigación"
        className="flex h-12 items-center gap-1 overflow-x-auto px-4 sm:px-6"
      >
        {ITEMS.map(({ href, label, exact }) => {
          const active = exact ? pathname === href : pathname.startsWith(href)
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex h-9 items-center rounded-md px-3 text-[14px] font-medium tracking-tight",
                "transition-colors duration-150 ease-out",
                "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2",
                active
                  ? "bg-[var(--bg-2)] text-foreground"
                  : "text-[var(--fg-3)] hover:bg-[var(--bg-2)]/40 hover:text-foreground",
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
