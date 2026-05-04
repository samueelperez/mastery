"use client"

import { useQueryClient } from "@tanstack/react-query"
import { ArrowLeft, Loader2, Mail, Shield } from "lucide-react"
import { useRouter, useSearchParams } from "next/navigation"
import { Suspense, useState } from "react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { authClient } from "@/lib/auth/auth-client"

const GOOGLE_ENABLED = process.env.NEXT_PUBLIC_GOOGLE_ENABLED === "true"

function GoogleIcon() {
  return (
    <svg viewBox="0 0 24 24" className="size-5" aria-hidden>
      <path
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"
        fill="#4285F4"
      />
      <path
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
        fill="#34A853"
      />
      <path
        d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
        fill="#FBBC05"
      />
      <path
        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
        fill="#EA4335"
      />
    </svg>
  )
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  )
}

function LoginForm() {
  const router = useRouter()
  const queryClient = useQueryClient()
  const searchParams = useSearchParams()
  const rawRedirect = searchParams.get("redirect") ?? "/"
  const redirectTo =
    rawRedirect.startsWith("/") && !rawRedirect.startsWith("//")
      ? rawRedirect
      : "/"
  const [showEmailForm, setShowEmailForm] = useState(false)
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)

  async function handleEmailLogin(e: React.FormEvent) {
    e.preventDefault()
    setError("")
    setLoading(true)
    const result = await authClient.signIn.email({ email, password })
    if (result.error) {
      setError(result.error.message ?? "Error al iniciar sesión")
      setLoading(false)
      return
    }
    queryClient.clear()
    window.location.assign(redirectTo)
  }

  if (showEmailForm) {
    return (
      <div className="w-full max-w-sm rounded-2xl bg-card p-8 ring-1 ring-border">
        <div className="mb-6 flex flex-col items-center gap-3">
          <button
            onClick={() => {
              setShowEmailForm(false)
              setError("")
            }}
            className="self-start text-muted-foreground transition-colors hover:text-foreground"
            aria-label="back"
          >
            <ArrowLeft className="size-5" />
          </button>
          <Shield className="size-10 text-primary" strokeWidth={1.5} aria-hidden />
          <h1 className="font-mono text-base tracking-tight text-foreground">
            Acceder con correo
          </h1>
        </div>

        <form onSubmit={handleEmailLogin} className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="email">Correo electrónico</Label>
            <Input
              id="email"
              type="email"
              placeholder="tu@email.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoFocus
            />
          </div>

          <div className="flex flex-col gap-2">
            <Label htmlFor="password">Contraseña</Label>
            <Input
              id="password"
              type="password"
              placeholder="••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>

          {error && <p className="text-sm text-destructive">{error}</p>}

          <Button type="submit" disabled={loading} className="w-full">
            {loading && <Loader2 className="size-4 animate-spin" />}
            Acceder
          </Button>

          <button
            type="button"
            onClick={() => router.push("/auth/login")}
            className="text-center text-xs text-muted-foreground transition-colors hover:text-foreground"
          >
            ¿Olvidaste tu contraseña?
          </button>
        </form>
      </div>
    )
  }

  return (
    <div className="w-full max-w-sm rounded-2xl bg-card p-8 ring-1 ring-border">
      <div className="mb-6 flex flex-col items-center gap-3">
        <Shield className="size-10 text-primary" strokeWidth={1.5} aria-hidden />
        <h1 className="font-mono text-base tracking-tight text-foreground">
          Acceder
        </h1>
        <p className="text-center text-xs text-muted-foreground">
          trading-copilot · interpreter and orchestrator, never an oracle.
        </p>
      </div>

      <div className="flex flex-col gap-3">
        {GOOGLE_ENABLED && (
          <Button
            variant="outline"
            size="lg"
            className="w-full justify-center gap-3"
            onClick={() => {
              authClient.signIn.social({
                provider: "google",
                callbackURL: redirectTo,
              })
            }}
          >
            <GoogleIcon />
            Acceder con Google
          </Button>
        )}

        <Button
          variant="outline"
          size="lg"
          className="w-full justify-center gap-3"
          onClick={() => setShowEmailForm(true)}
        >
          <Mail className="size-5" />
          Acceder con correo electrónico
        </Button>
      </div>
    </div>
  )
}
