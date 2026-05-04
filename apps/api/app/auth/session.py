"""Resolve a BetterAuth session cookie to a user_id.

Cookie format: `better-auth.session_token=<token>.<hmac>` (URL-encoded). The
`<token>` is the `session.token` column in Postgres. We don't reverify the HMAC
in Python — the DB row check (`expiresAt > now()`) is what gates validity.
The HMAC exists primarily so a tampered cookie fails before reaching the DB,
which is a defense-in-depth concern we can revisit if F4 ships to production.

Two FastAPI dependencies:
- `require_user_id`: returns the user_id or raises 401.
- `optional_user_id`: returns user_id or None — used by /health and /ws/market
  where data is non-sensitive.
"""

from __future__ import annotations

import urllib.parse
from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_dependency

log = structlog.get_logger(__name__)

SESSION_COOKIE_NAME = "better-auth.session_token"


def extract_session_token(cookie_value: str | None) -> str | None:
    """Strip the URL encoding and the `.<hmac>` suffix that BetterAuth appends."""
    if not cookie_value:
        return None
    decoded = urllib.parse.unquote(cookie_value)
    # Split off the trailing HMAC signature; the token has no `.` in it.
    if "." in decoded:
        token, _ = decoded.rsplit(".", 1)
        return token or None
    return decoded


async def resolve_user_id(
    request: Request,
    session: AsyncSession,
) -> str | None:
    """Look up the cookie's session row; return userId if active else None."""
    token = extract_session_token(request.cookies.get(SESSION_COOKIE_NAME))
    if token is None:
        return None
    row = (
        await session.execute(
            text(
                """
                SELECT "userId"
                FROM session
                WHERE token = :tok AND "expiresAt" > now()
                LIMIT 1
                """
            ),
            {"tok": token},
        )
    ).mappings().one_or_none()
    if row is None:
        return None
    return str(row["userId"])


async def require_user_id(
    request: Request,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> str:
    """FastAPI dependency: returns user_id or raises 401."""
    user_id = await resolve_user_id(request, session)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Cookie"},
        )
    return user_id


async def optional_user_id(
    request: Request,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> str | None:
    """FastAPI dependency: returns user_id or None — for endpoints that don't gate on auth."""
    return await resolve_user_id(request, session)
