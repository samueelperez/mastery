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

/** Railway / Neon / Supabase hosted Postgres requieren SSL pero usan certs
 *  managed (a veces self-signed). En local (docker-compose) no hay SSL.
 *  La heurística: si la connection string apunta a un host *.railway.app /
 *  *.rlwy.net / *.neon.tech / *.supabase.co o trae sslmode=require, usamos
 *  SSL con rejectUnauthorized=false. Si es localhost, sin SSL. */
function shouldUseSsl(url: string | undefined): boolean {
  if (!url) return false
  if (/sslmode=require/i.test(url)) return true
  if (/localhost|127\.0\.0\.1|::1/i.test(url)) return false
  // Hosted providers conocidos.
  if (/\.railway\.|\.rlwy\.net|\.neon\.tech|\.supabase\.co|\.aws\./i.test(url))
    return true
  // Default seguro en producción: si NODE_ENV === production, asume SSL.
  return process.env.NODE_ENV === "production"
}

const databaseUrl = process.env.DATABASE_URL

export const auth = betterAuth({
  database: new Pool({
    connectionString: databaseUrl,
    ssl: shouldUseSsl(databaseUrl)
      ? { rejectUnauthorized: false }
      : undefined,
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
