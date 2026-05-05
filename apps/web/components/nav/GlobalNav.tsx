"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"

import { cn } from "@/lib/utils"

import { AlertBell } from "./AlertBell"
import { BrandMark } from "./BrandMark"
import { UserMenu } from "./UserMenu"

interface NavItem {
  href: string
  label: string
  exact?: boolean
}

const ITEMS: NavItem[] = [
  { href: "/", label: "Chat", exact: true },
  { href: "/journal", label: "Diario" },
  { href: "/research", label: "Investigación" },
  { href: "/alerts", label: "Alertas" },
]

export function GlobalNav() {
  const pathname = usePathname()
  if (pathname?.startsWith("/auth")) return null

  return (
    <header
      className={cn(
        "relative z-30 flex h-14 shrink-0 items-center border-b border-border bg-card",
        "px-4 sm:px-6",
      )}
    >
      {/* Brand */}
      <Link
        href="/"
        className="group flex items-center gap-2.5 transition-opacity hover:opacity-80"
        aria-label="Mastery Trader · inicio"
      >
        <BrandMark size={24} />
        <span className="hidden font-sans text-[14px] tracking-tight sm:inline">
          <span className="font-semibold text-foreground">Mastery</span>{" "}
          <span className="font-medium text-[var(--fg-2)]">Trader</span>
        </span>
      </Link>

      {/* Separador editorial entre brand y nav */}
      <span
        aria-hidden
        className="mx-6 hidden h-5 w-px bg-border sm:block"
      />

      {/* Nav: text-only con underline-active animado */}
      <nav
        className="flex h-full items-center gap-1"
        aria-label="navegación principal"
      >
        {ITEMS.map(({ href, label, exact }) => {
          const active = exact ? pathname === href : pathname.startsWith(href)
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "relative flex h-full items-center px-3 text-[14px] font-medium tracking-tight",
                "transition-colors duration-150",
                "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-[-4px] focus-visible:rounded",
                active
                  ? "text-foreground"
                  : "text-[var(--fg-3)] hover:text-foreground",
              )}
            >
              {label}
              <span
                aria-hidden
                className={cn(
                  "absolute inset-x-3 -bottom-px h-[2px] rounded-t",
                  "transition-transform duration-200 ease-out",
                  active
                    ? "scale-x-100 bg-[var(--violet)]"
                    : "scale-x-0 bg-transparent",
                )}
              />
            </Link>
          )
        })}
      </nav>

      {/* Right cluster — sin contenedores, iconos puros */}
      <div className="ml-auto flex items-center gap-1.5">
        <AlertBell />
        <span aria-hidden className="mx-2 h-5 w-px bg-border" />
        <UserMenu />
      </div>
    </header>
  )
}
