"use client"

import { useQueryClient } from "@tanstack/react-query"
import { ArrowLeft, KeyRound, Loader2, Mail } from "lucide-react"
import { useRouter, useSearchParams } from "next/navigation"
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
import { authClient } from "@/lib/auth/auth-client"
import { cn } from "@/lib/utils"

const GOOGLE_ENABLED = process.env.NEXT_PUBLIC_GOOGLE_ENABLED === "true"

const CARD_BASE =
  "w-full max-w-sm rounded-xl bg-card/95 p-7 ring-1 ring-border shadow-2xl shadow-black/40 backdrop-blur-sm"
const CARD_ENTER =
  "animate-in fade-in slide-in-from-bottom-2 duration-200 ease-out motion-reduce:animate-none"

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
      <>
        <div className={cn(CARD_BASE, CARD_ENTER)}>
          <button
            type="button"
            onClick={() => {
              setShowEmailForm(false)
              setError("")
            }}
            className="-ml-1 mb-4 flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-widest text-muted-foreground transition-colors hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2"
          >
            <ArrowLeft className="size-3.5" aria-hidden />
            atrás
          </button>

          <div className="mb-6 flex flex-col items-center">
            <BrandWordmark caption="email + password" />
          </div>

          <form onSubmit={handleEmailLogin} className="flex flex-col gap-4">
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
                  autoFocus
                />
              </InputGroup>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label
                htmlFor="password"
                className="font-mono text-[11px] uppercase tracking-widest text-muted-foreground"
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

            <Button type="submit" disabled={loading} className="mt-1 w-full">
              {loading && <Loader2 className="size-4 animate-spin" aria-hidden />}
              Acceder
            </Button>

            <button
              type="button"
              onClick={() => router.push("/auth/login")}
              className="text-center font-mono text-[10px] uppercase tracking-widest text-muted-foreground transition-colors hover:text-foreground"
            >
              ¿olvidaste tu contraseña?
            </button>
          </form>
        </div>
      </>
    )
  }

  return (
    <>
      <div className={cn(CARD_BASE, CARD_ENTER)}>
        <div className="mb-6 flex flex-col items-center">
          <BrandWordmark caption="secure session" />
        </div>

        <div className="flex flex-col gap-2.5">
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
            <Mail className="size-5" aria-hidden />
            Acceder con correo electrónico
          </Button>
        </div>

        <p className="mt-6 text-center font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
          interpreter and orchestrator · never an oracle
        </p>
      </div>

      <LivePulse />
    </>
  )
}
