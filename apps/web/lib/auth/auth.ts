import { betterAuth } from "better-auth"
import { Pool } from "pg"

/**
 * Server-side BetterAuth instance. Uses the same Postgres database the FastAPI
 * backend reads, so the `session` table is the single source of truth: Next.js
 * writes it on signin/signout and FastAPI reads it via its own asyncpg pool.
 *
 * Google OAuth is enabled only when both env vars are present — keeps email-
 * only auth working without external setup.
 */
const googleEnabled = Boolean(
  process.env.GOOGLE_CLIENT_ID && process.env.GOOGLE_CLIENT_SECRET,
)

export const auth = betterAuth({
  database: new Pool({
    connectionString: process.env.DATABASE_URL,
  }),
  emailAndPassword: {
    enabled: true,
    autoSignIn: true,
    minPasswordLength: 8,
  },
  socialProviders: googleEnabled
    ? {
        google: {
          clientId: process.env.GOOGLE_CLIENT_ID!,
          clientSecret: process.env.GOOGLE_CLIENT_SECRET!,
        },
      }
    : undefined,
  // Cookies are scoped to the same origin Next.js runs on; FastAPI reads them
  // via the `better-auth.session_token` cookie (or the configured cookie name).
})
