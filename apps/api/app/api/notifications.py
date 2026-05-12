"""C.3 — Telegram notification endpoints.

Two endpoints:

1. `POST /notifications/telegram/bind-code` (authed) — issues a one-time code
   the user types into the bot to link their Telegram chat. Returns the
   code + TTL + a deep link `https://t.me/<bot>?start=<code>`.

2. `POST /telegram/webhook` (UNauthed, but secret-token guarded) — receives
   Telegram updates: `/start <code>` messages bind the chat, button presses
   call /setups/{id}/approve|reject internally.

Webhook security: Telegram sends the configured secret in the
`X-Telegram-Bot-Api-Secret-Token` header on every POST. The endpoint
constant-time-compares against `Settings.telegram_webhook_secret`.
"""

from __future__ import annotations

from secrets import compare_digest
from typing import Annotated, Any

import httpx
import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.auth import require_user_id
from app.config import get_settings
from app.db import session_scope
from app.notifications import bind as bind_flow
from app.notifications import telegram as tg
from app.storage.notification_repo import (
    get_telegram_chat_id,
    set_telegram_chat_id,
    unbind_telegram,
)

log = structlog.get_logger("api.notifications")
router = APIRouter()


# -----------------------------------------------------------------------------
# Bind code (user-facing, authed)
# -----------------------------------------------------------------------------


@router.post("/notifications/telegram/bind-code", tags=["notifications"])
async def issue_bind_code(
    user_id: Annotated[str, Depends(require_user_id)],
) -> dict[str, Any]:
    """Mint a one-time code + deep link so the user can /start the bot."""
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise HTTPException(
            status_code=503,
            detail="telegram bot not configured (TELEGRAM_BOT_TOKEN missing)",
        )
    code = await bind_flow.issue_bind_code(user_id)
    bot_username = await _resolve_bot_username()
    deep_link = (
        f"https://t.me/{bot_username}?start={code}" if bot_username else None
    )
    return {
        "code": code,
        "ttl_seconds": settings.telegram_bind_code_ttl_seconds,
        "deep_link": deep_link,
        "instructions_es": (
            f"Abre @{bot_username} en Telegram y envía `/start {code}` "
            f"(o pulsa el enlace) para vincular tu cuenta."
            if bot_username
            else f"Envía `/start {code}` al bot."
        ),
    }


@router.get("/notifications/telegram/status", tags=["notifications"])
async def telegram_status(
    user_id: Annotated[str, Depends(require_user_id)],
) -> dict[str, Any]:
    """Lightweight check: does this user have a linked Telegram chat?"""
    async with session_scope() as session:
        chat_id = await get_telegram_chat_id(session, user_id=user_id)
    return {"linked": chat_id is not None}


@router.delete("/notifications/telegram", tags=["notifications"])
async def unbind(
    user_id: Annotated[str, Depends(require_user_id)],
) -> dict[str, bool]:
    """User-initiated unbind. Idempotent."""
    async with session_scope() as session:
        unbound = await unbind_telegram(session, user_id=user_id)
    return {"unbound": unbound}


# -----------------------------------------------------------------------------
# Webhook (Telegram-facing, secret-token guarded)
# -----------------------------------------------------------------------------


@router.post("/telegram/webhook", tags=["telegram"])
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Annotated[
        str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")
    ] = None,
) -> dict[str, str]:
    """Receives every Telegram update for our bot. The secret token in the
    header is the only authentication we have — drop the request if missing
    or wrong. Always returns 200 to Telegram (so retries don't loop) even
    on errors we couldn't handle, but logs the failure."""
    settings = get_settings()
    expected = settings.telegram_webhook_secret
    if not expected:
        # Bot configured but no secret set → refuse rather than accept
        # un-authenticated webhooks. Forces explicit ops decision.
        raise HTTPException(
            status_code=503,
            detail="telegram webhook secret not configured",
        )
    if x_telegram_bot_api_secret_token is None or not compare_digest(
        x_telegram_bot_api_secret_token, expected
    ):
        # Don't leak whether the secret was missing vs wrong.
        raise HTTPException(status_code=401, detail="invalid secret")

    payload = await request.json()
    try:
        await _process_update(payload)
    except Exception as exc:
        # Log + return 200 so Telegram doesn't retry storm a bug.
        log.exception(
            "telegram.webhook.handler_error",
            error=f"{type(exc).__name__}: {exc}",
        )
    return {"ok": "true"}


# -----------------------------------------------------------------------------
# Update dispatch
# -----------------------------------------------------------------------------


async def _process_update(payload: dict[str, Any]) -> None:
    """Routes a Telegram update to the right handler. Two kinds we care about:

    - `message` with text matching `/start <code>` → bind flow.
    - `callback_query` from inline buttons → approve/reject the cited setup.

    Anything else is silently ignored (the bot doesn't do conversation)."""
    if "message" in payload:
        await _handle_message(payload["message"])
    elif "callback_query" in payload:
        await _handle_callback(payload["callback_query"])


async def _handle_message(message: dict[str, Any]) -> None:
    text = message.get("text", "") or ""
    chat = message.get("chat", {})
    chat_id = str(chat.get("id", ""))
    if not chat_id:
        return

    # /start <code> binds. Plain `/start` shows help.
    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        code = parts[1].strip() if len(parts) == 2 else ""
        if not code:
            await tg.send_text(
                chat_id,
                "Hola — para vincular tu cuenta, ve a la app web y genera "
                "un código en Ajustes → Notificaciones, luego envíalo aquí "
                "con `/start <CODIGO>`.",
            )
            return
        user_id = await bind_flow.consume_bind_code(code)
        if user_id is None:
            await tg.send_text(
                chat_id,
                "Código inválido o expirado. Genera uno nuevo en la app.",
            )
            return
        async with session_scope() as session:
            await set_telegram_chat_id(
                session, user_id=user_id, chat_id=chat_id
            )
        await tg.send_text(
            chat_id,
            "✅ Cuenta vinculada. Te enviaré aquí las propuestas del scout "
            "para que apruebes o rechaces en un toque.",
        )
        log.info("telegram.bind.ok", user_id=user_id, chat_id=chat_id)
        return

    # Anything else: gentle nudge.
    await tg.send_text(
        chat_id,
        "Para vincular tu cuenta usa `/start <CODIGO>` con el código que "
        "obtienes en la app.",
    )


async def _handle_callback(cb: dict[str, Any]) -> None:
    """Approve/Reject from inline buttons. callback_data shape: `a:<setup>` or
    `r:<setup>`. We hit our own /setups endpoints via httpx (rather than
    importing the handlers) so auth and business logic stay in one place."""
    callback_id = cb.get("id", "")
    data = cb.get("data", "") or ""
    chat = (cb.get("message") or {}).get("chat", {})
    chat_id = str(chat.get("id", ""))

    if not data or ":" not in data or not chat_id:
        await tg.answer_callback(callback_id, "Botón no reconocido")
        return

    action, setup_id = data.split(":", 1)
    if action not in {"a", "r"}:
        await tg.answer_callback(callback_id, "Acción desconocida")
        return

    # Resolve which user this chat is bound to. We need their user_id to
    # authenticate the API call (no Bearer token in webhook context).
    user_id = await _resolve_user_from_chat(chat_id)
    if user_id is None:
        await tg.answer_callback(
            callback_id,
            "Chat no vinculado — usa /start <CODIGO> primero",
        )
        return

    # Internal call: shortcut the auth by using the same session_scope
    # the endpoint would have. We INSERT the same audit event directly.
    from app.api.setups import approve_setup, reject_setup

    try:
        if action == "a":
            await approve_setup(setup_id, user_id)
            await tg.answer_callback(callback_id, "✅ Aprobado")
        else:
            await reject_setup(setup_id, user_id)
            await tg.answer_callback(callback_id, "❌ Rechazado")
    except HTTPException as exc:
        await tg.answer_callback(callback_id, f"Error: {exc.detail}")
    except Exception as exc:
        log.exception(
            "telegram.callback.error",
            error=f"{type(exc).__name__}: {exc}",
            setup_id=setup_id,
        )
        await tg.answer_callback(callback_id, "Error inesperado")


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


_bot_username_cache: str | None = None


async def _resolve_bot_username() -> str | None:
    """Calls Telegram `getMe` once and caches the username. Used to render
    a clickable deep link in the bind-code response."""
    global _bot_username_cache
    if _bot_username_cache is not None:
        return _bot_username_cache
    base = (
        f"https://api.telegram.org/bot{get_settings().telegram_bot_token}"
        if get_settings().telegram_bot_token
        else None
    )
    if base is None:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{base}/getMe")
        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok"):
                username = data.get("result", {}).get("username")
                if isinstance(username, str):
                    _bot_username_cache = username
                    return username
    except httpx.HTTPError as exc:
        log.warning("telegram.getme.failed", error=str(exc))
    return None


async def _resolve_user_from_chat(chat_id: str) -> str | None:
    """Reverse lookup: which user_id owns this Telegram chat? Simple table
    scan since the cardinality is low (one row per user)."""
    from sqlalchemy import text

    async with session_scope() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT user_id FROM user_notification_settings
                    WHERE telegram_chat_id = :cid
                    LIMIT 1
                    """
                ),
                {"cid": chat_id},
            )
        ).scalar_one_or_none()
    return row if isinstance(row, str) else None
