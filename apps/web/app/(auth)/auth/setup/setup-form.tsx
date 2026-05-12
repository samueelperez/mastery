"use client"

import { useQueryClient } from "@tanstack/react-query"
import { ArrowRight, KeyRound, Loader2, UserIcon } from "lucide-react"
import { useState } from "react"

import { BrandWordmark } from "@/components/auth/BrandWordmark"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
} from "@/components/ui/input-group"
import { Label } from "@/components/ui/label"
import { authClient } from "@/lib/core/auth/auth-client"
import { cn } from "@/lib/core/utils"

export function SetupForm() {
  const queryClient = useQueryClient()
  const [name, setName] = useState("")
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError("")
    setLoading(true)
    const result = await authClient.signUp.email({ name, email, password })
    if (result.error) {
      setError(result.error.message ?? "Error al crear la cuenta")
      setLoading(false)
      return
    }
    queryClient.clear()
    window.location.assign("/")
  }

  return (
    <div className="flex flex-col gap-6 motion-reduce:animate-none animate-in fade-in slide-in-from-bottom-2 duration-200 ease-out">
      <BrandWordmark caption="bootstrap · primer usuario" />

      <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--fg-3)]">
        <span className="dot dot-amber" aria-hidden />
        <span>paso 00</span>
        <span className="text-[var(--fg-4)]">·</span>
        <span className="text-[var(--fg-1)]">crear owner</span>
      </div>

      <h1 className="font-mono text-[22px] tracking-tight text-foreground">
        Inicializa el sistema
      </h1>

      <p className="font-sans text-[13px] leading-relaxed text-[var(--fg-2)]">
        Inicialización única. Tras crear esta cuenta, /auth/setup desaparece y
        el sistema queda como single-user contigo como owner.
      </p>

      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <Label
            htmlFor="name"
            className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--fg-3)]"
          >
            Nombre
          </Label>
          <InputGroup>
            <InputGroupAddon>
              <UserIcon aria-hidden />
            </InputGroupAddon>
            <InputGroupInput
              id="name"
              type="text"
              placeholder="Samuel"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              autoFocus
            />
          </InputGroup>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label
            htmlFor="email"
            className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--fg-3)]"
          >
            Correo electrónico
          </Label>
          <InputGroup>
            <InputGroupAddon>
              <span
                aria-hidden
                className="font-mono text-[14px] text-[var(--violet)]"
              >
                @
              </span>
            </InputGroupAddon>
            <InputGroupInput
              id="email"
              type="email"
              placeholder="tu@email.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </InputGroup>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label
            htmlFor="password"
            className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--fg-3)]"
          >
            Contraseña (mín. 8)
          </Label>
          <InputGroup>
            <InputGroupAddon>
              <KeyRound aria-hidden />
            </InputGroupAddon>
            <InputGroupInput
              id="password"
              type="password"
              placeholder="••••••••"
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </InputGroup>
        </div>

        {error && (
          <p
            role="alert"
            className="font-mono text-[11px] uppercase tracking-widest text-destructive"
          >
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={
            loading ||
            name.length === 0 ||
            email.length === 0 ||
            password.length < 8
          }
          className={cn(
            "group mt-1 flex h-11 items-center justify-center gap-2 rounded-md",
            "bg-[var(--amber)] text-[var(--bg-0)]",
            "font-mono text-[12px] font-semibold uppercase tracking-[0.18em]",
            "transition-all duration-150 hover:brightness-110",
            "disabled:cursor-not-allowed disabled:opacity-40",
            "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2",
          )}
        >
          {loading ? (
            <Loader2 className="size-4 animate-spin" aria-hidden />
          ) : (
            <>
              <span>Crear cuenta</span>
              <ArrowRight
                className="size-4 transition-transform duration-150 group-hover:translate-x-0.5"
                aria-hidden
              />
            </>
          )}
        </button>
      </form>
    </div>
  )
}
