"""Valkey pub/sub thin wrapper, used to fan out market updates from the
ingestion task to N WebSocket subscribers.

We use redis-py against Valkey (100% wire-protocol compatible). Channels are
named `mkt:{exchange}:{symbol_lc}:k:{timeframe}` so the routing is trivial.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import orjson
import redis.asyncio as redis
import structlog

from app.config import get_settings

log = structlog.get_logger(__name__)

_client: redis.Redis | None = None


def get_client() -> redis.Redis:
    global _client
    if _client is None:
        settings = get_settings()
        _client = redis.from_url(settings.valkey_url, decode_responses=True)
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def market_channel(*, exchange: str, symbol: str, timeframe: str) -> str:
    return f"mkt:{exchange}:{symbol.lower()}:k:{timeframe}"


async def publish_json(channel: str, payload: object) -> int:
    client = get_client()
    raw = orjson.dumps(payload).decode()
    return int(await client.publish(channel, raw))


@asynccontextmanager
async def subscribe(channel: str) -> AsyncIterator[redis.client.PubSub]:
    """Async context manager that yields a pubsub already subscribed to `channel`.

    Use:
        async with subscribe("mkt:...:k:1m") as ps:
            async for msg in ps.listen():
                if msg["type"] != "message": continue
                data = orjson.loads(msg["data"])
                ...
    """
    client = get_client()
    pubsub = client.pubsub()
    await pubsub.subscribe(channel)
    try:
        yield pubsub
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()  # type: ignore[no-untyped-call]


async def ping() -> bool:
    """Returns True if Valkey responds to PING; False otherwise. Best-effort."""
    try:
        client = get_client()
        # `client.ping()` is async on redis.asyncio; mypy's stubs flag the
        # union return type loosely.
        result: object = await client.ping()  # type: ignore[misc]
        return bool(result)
    except Exception:
        return False
