"use client"

import { useQueryClient } from "@tanstack/react-query"
import { ArrowRight, KeyRound, Loader2 } from "lucide-react"
import { useSearchParams } from "next/navigation"
import { Suspense, useState } from "react"

import { BrandWordmark } from "@/components/auth/BrandWordmark"
import { LivePulse } from "@/components/auth/LivePulse"
import { Button } from "@/components/ui/button"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
} from "@/components/ui/input-group"
import { Label } from "@/components/ui/label"
import { authClient } from "@/lib/core/auth/auth-client"
import { cn } from "@/lib/core/utils"

const GOOGLE_ENABLED = process.env.NEXT_PUBLIC_GOOGLE_ENABLED === "true"

function GoogleIcon() {
  return (
    <svg viewBox="0 0 24 24" className="size-4" aria-hidden>
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

function GitHubIcon() {
  return (
    <svg viewBox="0 0 24 24" className="size-4" aria-hidden fill="currentColor">
      <path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.39 7.86 10.91.58.11.79-.25.79-.55v-2.04c-3.2.7-3.87-1.36-3.87-1.36-.52-1.31-1.27-1.66-1.27-1.66-1.04-.71.08-.7.08-.7 1.15.08 1.76 1.18 1.76 1.18 1.02 1.75 2.68 1.24 3.34.95.1-.74.4-1.24.72-1.53-2.55-.29-5.24-1.27-5.24-5.66 0-1.25.45-2.27 1.18-3.07-.12-.29-.51-1.46.11-3.04 0 0 .96-.31 3.15 1.17a10.94 10.94 0 0 1 5.74 0c2.19-1.48 3.15-1.17 3.15-1.17.62 1.58.23 2.75.11 3.04.74.8 1.18 1.82 1.18 3.07 0 4.4-2.69 5.36-5.25 5.65.41.36.78 1.06.78 2.13v3.16c0 .31.21.67.8.55C20.21 21.39 23.5 17.08 23.5 12 23.5 5.65 18.35.5 12 .5z" />
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
  const queryClient = useQueryClient()
  const searchParams = useSearchParams()
  const rawRedirect = searchParams.get("redirect") ?? "/"
  const redirectTo =
    rawRedirect.startsWith("/") && !rawRedirect.startsWith("//")
      ? rawRedirect
      : "/"
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
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

  return (
    <>
      <div className="flex flex-col gap-6 motion-reduce:animate-none animate-in fade-in slide-in-from-bottom-2 duration-200 ease-out">
        <BrandWordmark caption="acceso · sesión segura" />

        {/* Step indicator */}
        <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--fg-3)]">
          <span className="dot dot-amber" aria-hidden />
          <span>paso 01</span>
          <span className="text-[var(--fg-4)]">·</span>
          <span className="text-[var(--fg-1)]">acceder</span>
        </div>

        <h1 className="font-mono text-[22px] tracking-tight text-foreground">
          Entra a tu cabina
        </h1>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
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
                autoFocus
              />
            </InputGroup>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label
              htmlFor="password"
              className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--fg-3)]"
            >
              Contraseña
            </Label>
            <InputGroup>
              <InputGroupAddon>
                <KeyRound aria-hidden />
              </InputGroupAddon>
              <InputGroupInput
                id="password"
                type="password"
                placeholder="••••••••"
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
            disabled={loading || email.length === 0 || password.length === 0}
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
                <span>Acceder</span>
                <ArrowRight
                  className="size-4 transition-transform duration-150 group-hover:translate-x-0.5"
                  aria-hidden
                />
              </>
            )}
          </button>
        </form>

        <div className="flex items-center gap-3">
          <span className="h-px flex-1 bg-border" aria-hidden />
          <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--fg-3)]">
            o continúa con
          </span>
          <span className="h-px flex-1 bg-border" aria-hidden />
        </div>

        <div
          className={cn(
            "grid gap-2",
            GOOGLE_ENABLED ? "grid-cols-2" : "grid-cols-1",
          )}
        >
          {GOOGLE_ENABLED && (
            <Button
              type="button"
              variant="outline"
              className="h-10 justify-center gap-2 font-mono text-[11px] uppercase tracking-[0.1em]"
              onClick={() => {
                authClient.signIn.social({
                  provider: "google",
                  callbackURL: redirectTo,
                })
              }}
            >
              <GoogleIcon />
              Google
            </Button>
          )}
          <Button
            type="button"
            variant="outline"
            disabled
            className="h-10 justify-center gap-2 font-mono text-[11px] uppercase tracking-[0.1em] opacity-60"
            title="próximamente"
          >
            <GitHubIcon />
            GitHub
          </Button>
        </div>

        <p className="text-center font-mono text-[10px] leading-relaxed tracking-[0.06em] text-[var(--fg-3)]">
          al acceder aceptas el flujo determinista —
          <br />
          el copilot interpreta y orquesta, nunca predice.
        </p>
      </div>

      {/* Mobile only — desktop renders LivePulse inside AuthShowcase */}
      <div className="mt-8 lg:hidden">
        <LivePulse />
      </div>
    </>
  )
}
