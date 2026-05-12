"""C.3 — Telegram bind flow.

The bind code is a one-time short string the user types into the bot. The
bot's `/start <code>` (or plain message with the code) triggers a webhook
where we look up the code → user_id mapping and persist the chat_id.

Storage: Redis (Valkey) with TTL. Idempotent: re-issuing a code for the
same user invalidates the previous one (last-issuance wins).

Code shape: 6 alphanumeric chars (uppercase + digits), human-readable on
mobile, ~36^6 = 2.1B combinations — vastly more than concurrent live
codes at any TTL window, so collisions are practically zero. We retry
once on the off-chance of a collision.
"""

from __future__ import annotations

import secrets
import string

from app.broadcasting.pubsub import get_client
from app.config import get_settings

_CODE_ALPHABET = string.ascii_uppercase + string.digits


def _redis_key(code: str) -> str:
    return f"telegram:bind:{code}"


def _generate_code() -> str:
    # Avoid visually confusable chars (O/0, I/1) to reduce mistyping in TG.
    safe = "".join(c for c in _CODE_ALPHABET if c not in {"O", "0", "I", "1"})
    return "".join(secrets.choice(safe) for _ in range(6))


async def issue_bind_code(user_id: str) -> str:
    """Generates a fresh code and stores `code → user_id` in Redis with TTL.

    Re-calling for the same user invalidates the previous code (we don't
    track them; the SETEX with a new code shadows the old until it expires).
    """
    ttl = get_settings().telegram_bind_code_ttl_seconds
    client = get_client()
    for _ in range(3):  # tiny collision retry loop
        code = _generate_code()
        # NX = only set if not exists, so a collision returns False.
        ok = await client.set(_redis_key(code), user_id, ex=ttl, nx=True)
        if ok:
            return code
    # Pathological: 3 collisions in a row. Should never happen with 6-char
    # alphabet; raise so caller can surface the issue.
    raise RuntimeError("could not allocate unique bind code (3 collisions)")


async def consume_bind_code(code: str) -> str | None:
    """Atomic look-up + delete. Returns the user_id the code was issued to,
    or None if the code is unknown / expired / already consumed.

    Uses GETDEL (Redis 6.2+) for atomicity — a concurrent /start with the
    same code from two devices can't both succeed."""
    client = get_client()
    raw = await client.getdel(_redis_key(code))
    if raw is None:
        return None
    if isinstance(raw, bytes):
        return raw.decode("utf-8")
    return str(raw)
