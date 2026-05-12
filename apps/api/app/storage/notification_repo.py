"""C.3 — CRUD para `user_notification_settings` (migración 019).

Mínimo viable: get/set del `telegram_chat_id` por user_id. La tabla también
incluye `webpush_subscriptions` jsonb pero el web push queda pendiente.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def set_telegram_chat_id(
    session: AsyncSession, *, user_id: str, chat_id: str
) -> None:
    """Vincula un chat_id a un user_id. Upsert — si el user ya tenía un
    chat_id (re-bind tras cambiar de cuenta de Telegram), se sobrescribe."""
    await session.execute(
        text(
            """
            INSERT INTO user_notification_settings (
                user_id, telegram_chat_id, telegram_linked_at,
                webpush_subscriptions
            )
            VALUES (:uid, :chat_id, now(), '[]'::jsonb)
            ON CONFLICT (user_id) DO UPDATE
            SET telegram_chat_id = EXCLUDED.telegram_chat_id,
                telegram_linked_at = now(),
                updated_at = now()
            """
        ),
        {"uid": user_id, "chat_id": chat_id},
    )


async def get_telegram_chat_id(
    session: AsyncSession, *, user_id: str
) -> str | None:
    """Lee el chat_id vinculado o None si el usuario no ha hecho bind."""
    row = (
        await session.execute(
            text(
                """
                SELECT telegram_chat_id
                FROM user_notification_settings
                WHERE user_id = :uid
                """
            ),
            {"uid": user_id},
        )
    ).scalar_one_or_none()
    if row is None or not isinstance(row, str):
        return None
    return str(row)


async def unbind_telegram(
    session: AsyncSession, *, user_id: str
) -> bool:
    """Limpia el chat_id. Devuelve True si había uno (usuario lo desvinculó)."""
    result = await session.execute(
        text(
            """
            UPDATE user_notification_settings
            SET telegram_chat_id = NULL,
                telegram_linked_at = NULL,
                updated_at = now()
            WHERE user_id = :uid AND telegram_chat_id IS NOT NULL
            """
        ),
        {"uid": user_id},
    )
    return (result.rowcount or 0) > 0  # type: ignore[attr-defined]
