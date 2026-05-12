"use client"

import { useQueryClient } from "@tanstack/react-query"
import { LogOutIcon, MoonIcon, SunIcon, UserIcon } from "lucide-react"
import { useTheme } from "next-themes"
import { useEffect, useState } from "react"

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { authClient, clearBearerToken } from "@/lib/core/auth/auth-client"
import { cn } from "@/lib/core/utils"

function initials(input: string | null | undefined): string {
  if (!input) return "?"
  const trimmed = input.trim()
  if (!trimmed) return "?"
  const parts = trimmed.split(/\s+/)
  if (parts.length >= 2) {
    return (parts[0]![0] + parts[parts.length - 1]![0]).toUpperCase()
  }
  return trimmed.slice(0, 2).toUpperCase()
}

/** Reserve the same 32×32 footprint server-side and pre-mount client-side, so
 * the navbar layout doesn't shift when the session resolves. The empty
 * placeholder is byte-identical between SSR and first client render → no
 * hydration warning. */
function UserMenuPlaceholder() {
  return <span className="size-9" aria-hidden />
}

export function UserMenu() {
  const [mounted, setMounted] = useState(false)
  useEffect(() => setMounted(true), [])

  const { data, isPending } = authClient.useSession()
  const queryClient = useQueryClient()
  const { resolvedTheme, setTheme } = useTheme()
  const isDark = resolvedTheme === "dark"

  // Defer rendering session-dependent UI until after hydration: SSR + first
  // client paint always render the placeholder, the actual menu mounts on the
  // second tick. authClient.useSession resolves from the cookie at different
  // moments on server vs. client, which is exactly the hydration mismatch
  // pattern the React error message describes.
  if (!mounted || isPending) {
    return <UserMenuPlaceholder />
  }
  if (!data?.user) return null

  const { name, email, image } = data.user
  const initialsLabel = initials(name ?? email)

  async function handleSignOut() {
    await authClient.signOut()
    clearBearerToken()
    queryClient.clear()
    window.location.assign("/auth/login")
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={`Menú de cuenta de ${name ?? email}`}
          className={cn(
            "grid size-9 place-items-center rounded-full border border-border",
            "bg-[linear-gradient(135deg,var(--violet)_0%,oklch(0.55_0.18_320)_100%)]",
            "font-mono text-[12px] font-semibold uppercase tracking-wide text-white",
            "transition-shadow duration-150 hover:shadow-[0_0_0_2px_var(--violet-soft)]",
            "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2",
          )}
        >
          {image ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={image}
              alt=""
              className="size-full rounded-full object-cover"
            />
          ) : (
            initialsLabel
          )}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" sideOffset={8} className="min-w-[15rem]">
        <DropdownMenuLabel className="font-normal">
          <div className="flex flex-col gap-0.5">
            <span className="text-[14px] font-medium text-foreground">
              {name ?? "—"}
            </span>
            <span className="font-mono text-[11px] tracking-wide text-muted-foreground">
              {email}
            </span>
          </div>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          className="text-[14px]"
          disabled
          aria-disabled
        >
          <UserIcon className="size-4" aria-hidden />
          perfil (próximamente)
        </DropdownMenuItem>
        <DropdownMenuItem
          className="text-[14px]"
          onSelect={(e) => {
            e.preventDefault()
            setTheme(isDark ? "light" : "dark")
          }}
        >
          {isDark ? (
            <SunIcon className="size-4" aria-hidden />
          ) : (
            <MoonIcon className="size-4" aria-hidden />
          )}
          {isDark ? "modo claro" : "modo oscuro"}
        </DropdownMenuItem>
        <DropdownMenuItem onClick={handleSignOut} className="text-[14px]">
          <LogOutIcon className="size-4" aria-hidden />
          cerrar sesión
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
