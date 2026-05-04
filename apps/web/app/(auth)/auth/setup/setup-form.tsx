"use client"

import { useQueryClient } from "@tanstack/react-query"
import { Loader2, Shield } from "lucide-react"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { authClient } from "@/lib/auth/auth-client"

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
    <div className="w-full max-w-sm rounded-2xl bg-card p-8 ring-1 ring-border">
      <div className="mb-6 flex flex-col items-center gap-3">
        <Shield className="size-10 text-primary" strokeWidth={1.5} aria-hidden />
        <h1 className="font-mono text-base tracking-tight text-foreground">
          First-user setup
        </h1>
        <p className="text-center text-xs text-muted-foreground">
          One-shot bootstrap. After this account is created, /auth/setup is gone
          and the system is single-user with you as the owner.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <div className="flex flex-col gap-2">
          <Label htmlFor="name">Nombre</Label>
          <Input
            id="name"
            type="text"
            placeholder="Samuel"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            autoFocus
          />
        </div>

        <div className="flex flex-col gap-2">
          <Label htmlFor="email">Correo electrónico</Label>
          <Input
            id="email"
            type="email"
            placeholder="tu@email.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </div>

        <div className="flex flex-col gap-2">
          <Label htmlFor="password">Contraseña (mín. 8)</Label>
          <Input
            id="password"
            type="password"
            placeholder="••••••••"
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </div>

        {error && <p className="text-sm text-destructive">{error}</p>}

        <Button type="submit" disabled={loading} className="w-full">
          {loading && <Loader2 className="size-4 animate-spin" />}
          Crear cuenta
        </Button>
      </form>
    </div>
  )
}
