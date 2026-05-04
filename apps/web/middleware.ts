import { getSessionCookie } from "better-auth/cookies"
import { NextResponse, type NextRequest } from "next/server"

/** Auth-gating middleware — redirects unauthenticated requests to /auth/login.
 *
 * Cheap path: read the session cookie from the request. We don't validate it
 * against the DB here (that happens in FastAPI / on first authClient call).
 * Cookie tampering at this layer is fine because the actual auth check on
 * the server-side rejects forged cookies — the middleware only decides
 * whether to redirect, not whether to grant access. */
export function middleware(request: NextRequest) {
  const sessionCookie = getSessionCookie(request)
  if (!sessionCookie) {
    const url = request.nextUrl.clone()
    url.pathname = "/auth/login"
    url.searchParams.set("redirect", request.nextUrl.pathname + request.nextUrl.search)
    return NextResponse.redirect(url)
  }
  return NextResponse.next()
}

export const config = {
  // All routes EXCEPT auth pages, BetterAuth API, and Next internals.
  // The matcher uses negative-lookaheads on the path.
  matcher: [
    "/((?!auth|api/auth|_next/static|_next/image|favicon.ico).*)",
  ],
}
