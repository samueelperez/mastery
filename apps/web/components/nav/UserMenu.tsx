"use client"

import { useQueryClient } from "@tanstack/react-query"
import { LogOutIcon, UserIcon } from "lucide-react"

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { authClient } from "@/lib/auth/auth-client"
import { cn } from "@/lib/utils"

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

export function UserMenu() {
  const { data, isPending } = authClient.useSession()
  const queryClient = useQueryClient()

  if (isPending) {
    return (
      <span
        className="flex size-8 items-center justify-center rounded-full bg-muted text-muted-foreground"
        aria-busy="true"
      />
    )
  }
  if (!data?.user) return null

  const { name, email, image } = data.user
  const initialsLabel = initials(name ?? email)

  async function handleSignOut() {
    await authClient.signOut()
    queryClient.clear()
    window.location.assign("/auth/login")
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={`Account menu for ${name ?? email}`}
          className={cn(
            "flex size-8 items-center justify-center rounded-full",
            "bg-accent/30 font-mono text-[11px] font-medium uppercase tracking-wide text-foreground",
            "transition-colors duration-150 hover:bg-accent/50",
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
      <DropdownMenuContent align="end" sideOffset={6} className="min-w-[14rem]">
        <DropdownMenuLabel className="font-normal">
          <div className="flex flex-col gap-0.5">
            <span className="font-mono text-xs text-foreground">
              {name ?? "—"}
            </span>
            <span className="font-mono text-[10px] tracking-wide text-muted-foreground">
              {email}
            </span>
          </div>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          className="font-mono text-xs"
          disabled
          aria-disabled
        >
          <UserIcon className="size-3.5" aria-hidden />
          profile (soon)
        </DropdownMenuItem>
        <DropdownMenuItem onClick={handleSignOut} className="font-mono text-xs">
          <LogOutIcon className="size-3.5" aria-hidden />
          sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
