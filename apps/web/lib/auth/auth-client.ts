"use client"

import { createAuthClient } from "better-auth/react"

/** Browser BetterAuth client. Same name (`authClient`) as LienzzoSuite so the
 * cloned login page stays drop-in. baseURL omitted → uses current origin's
 * `/api/auth/*` route handler. */
export const authClient = createAuthClient()

export const { signIn, signUp, signOut, useSession } = authClient
