"""Resolve a BetterAuth session to a user_id.

Two formas de transportar el token desde el browser:

1. Cookie `better-auth.session_token=<token>.<hmac>` (URL-encoded) —
   funciona cuando frontend y API comparten dominio (dev local same-origin
   o custom domain `*.midominio.com`).
2. Header `Authorization: Bearer <token>` — emitido por el plugin
   `bearer()` de BetterAuth y persistido en localStorage por el cliente.
   Necesario en deploys cross-domain (Vercel ↔ Railway) donde el browser
   no envía cookies entre dominios distintos.

`<token>` en ambos casos es el `session.token` de Postgres. No reverificamos
HMAC en Python — el row check (`expiresAt > now()`) es el gate de validez.
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


def extract_bearer_token(authorization: str | None) -> str | None:
    """Read `Authorization: Bearer <token>` (plugin bearer de BetterAuth).
    Devuelve el token raw — sin sufijo HMAC, ya viene limpio del plugin."""
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def resolve_token_from_request(request: Request) -> str | None:
    """Prefiere el header Authorization (cross-domain) sobre la cookie."""
    bearer = extract_bearer_token(request.headers.get("authorization"))
    if bearer:
        return bearer
    return extract_session_token(request.cookies.get(SESSION_COOKIE_NAME))


async def lookup_user_id_for_token(
    token: str, session: AsyncSession
) -> str | None:
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


async def resolve_user_id(
    request: Request,
    session: AsyncSession,
) -> str | None:
    """Lee token desde header Authorization o cookie, devuelve userId o None."""
    token = resolve_token_from_request(request)
    if token is None:
        return None
    return await lookup_user_id_for_token(token, session)


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
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user_id


async def optional_user_id(
    request: Request,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> str | None:
    """FastAPI dependency: returns user_id or None — for endpoints that don't gate on auth."""
    return await resolve_user_id(request, session)
