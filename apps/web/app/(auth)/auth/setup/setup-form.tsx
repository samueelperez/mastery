"use client"

import { useQueryClient } from "@tanstack/react-query"
import { KeyRound, Loader2, Mail, UserIcon } from "lucide-react"
import { useState } from "react"

import { BrandWordmark } from "@/components/auth/BrandWordmark"
import { Button } from "@/components/ui/button"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
} from "@/components/ui/input-group"
import { Label } from "@/components/ui/label"
import { authClient } from "@/lib/auth/auth-client"
import { cn } from "@/lib/utils"

const CARD_BASE =
  "w-full max-w-md rounded-xl bg-card/95 p-8 ring-1 ring-border shadow-2xl shadow-black/40 backdrop-blur-sm"
const CARD_ENTER =
  "animate-in fade-in slide-in-from-bottom-2 duration-200 ease-out motion-reduce:animate-none"

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
    <div className={cn(CARD_BASE, CARD_ENTER)}>
      <div className="mb-6 flex flex-col items-center">
        <BrandWordmark caption="configuración inicial" />
      </div>

      <p className="mb-6 text-center text-xs leading-relaxed text-muted-foreground">
        Inicialización única. Tras crear esta cuenta, /auth/setup desaparece y
        el sistema queda como single-user contigo como owner.
      </p>

      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <Label
            htmlFor="name"
            className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground"
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
            className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground"
          >
            Correo electrónico
          </Label>
          <InputGroup>
            <InputGroupAddon>
              <Mail aria-hidden />
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
            className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground"
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

        <Button type="submit" disabled={loading} className="mt-1 w-full">
          {loading && <Loader2 className="size-4 animate-spin" aria-hidden />}
          Crear cuenta
        </Button>
      </form>
    </div>
  )
}
