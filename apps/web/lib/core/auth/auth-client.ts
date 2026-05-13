"use client"

import { createAuthClient } from "better-auth/react"

/** Storage key for the BetterAuth bearer token. Same dominio en el cliente
 * que en `lib/api.ts` para que el `apiFetch` lo recupere y lo mande en el
 * header `Authorization: Bearer <token>` cuando llama a la API en otro
 * dominio (Railway). Cookies cross-domain no funcionan sin custom domain
 * compartido, así que vamos por token-based mientras tanto. */
export const BEARER_TOKEN_KEY = "mt.bearer_token"

function readToken(): string {
  if (typeof window === "undefined") return ""
  return window.localStorage.getItem(BEARER_TOKEN_KEY) ?? ""
}

function persistTokenFromResponse(response: Response | undefined): void {
  if (typeof window === "undefined") return
  const token = response?.headers.get("set-auth-token")
  if (token) window.localStorage.setItem(BEARER_TOKEN_KEY, token)
}

/** Browser BetterAuth client. baseURL omitted → uses current origin's
 * `/api/auth/*` route handler. fetchOptions wires the bearer plugin both
 * directions: token persistido en localStorage tras signIn, y reenviado
 * como Authorization header en cada request al propio /api/auth/* (para
 * que el handler interno del Next reconozca al usuario sin cookie). */
export const authClient = createAuthClient({
  fetchOptions: {
    auth: {
      type: "Bearer",
      token: () => readToken(),
    },
    onSuccess: (ctx) => {
      persistTokenFromResponse(ctx.response)
    },
  },
})

export const { signIn, signUp, signOut, useSession } = authClient

export function clearBearerToken(): void {
  if (typeof window === "undefined") return
  window.localStorage.removeItem(BEARER_TOKEN_KEY)
}
