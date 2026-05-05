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

/** Regla simple: SI la URL apunta a localhost → sin SSL (docker-compose).
 *  En cualquier otro caso → SSL con rejectUnauthorized=false. Esto cubre
 *  Railway / Neon / Supabase / RDS / cualquier hosted Postgres sin tener
 *  que mantener una whitelist frágil de dominios.
 *
 *  rejectUnauthorized=false acepta certs self-signed o managed por el
 *  provider — necesario porque Railway no expone su CA pública. */
function isLocalDb(url: string | undefined): boolean {
  if (!url) return false
  return /localhost|127\.0\.0\.1|::1|host\.docker\.internal/i.test(url)
}

const databaseUrl = process.env.DATABASE_URL

export const auth = betterAuth({
  database: new Pool({
    connectionString: databaseUrl,
    ssl: isLocalDb(databaseUrl)
      ? false
      : { rejectUnauthorized: false },
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
