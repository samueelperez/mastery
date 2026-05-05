import { notFound } from "next/navigation"
import { Pool } from "pg"

import { SetupForm } from "./setup-form"

// La página hace una query a Postgres en cada request. NUNCA debe
// prerenderizarse en build time — en Vercel el build no tiene la DB
// accesible y aunque la tuviera, queremos comprobar el count fresh en
// cada visita (la página se "auto-destruye" cuando aparece el primer user).
export const dynamic = "force-dynamic"

/** First-user bootstrap. Self-destructs once any row exists in `"user"` so the
 * route can't be used as an open registration backdoor. Server component:
 * the count check runs on Node, the form is the only client surface. */
async function userCount(): Promise<number> {
  const pool = new Pool({ connectionString: process.env.DATABASE_URL })
  try {
    const { rows } = await pool.query<{ n: string }>(
      'SELECT count(*)::text AS n FROM "user"',
    )
    return Number(rows[0]?.n ?? 0)
  } finally {
    await pool.end()
  }
}

export default async function SetupPage() {
  const n = await userCount()
  if (n > 0) notFound()
  return <SetupForm />
}
