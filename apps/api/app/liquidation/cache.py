"""Valkey caching helpers for the liquidation HTTP endpoints.

Thin wrapper over ``app.core.broadcasting.pubsub.get_client()``. Two helper
pairs keyed by ``(symbol, timeframe)`` for snapshots and by
``(symbol, timeframe, lookback_h, min_vol)`` for history. The TTLs sit
comfortably under the natural cadence of the snapshot scheduler (every 2
min) so the cache absorbs the ~3s worst-case latency of HeatmapService
without ever serving truly stale data.

All cache operations are best-effort: a Redis failure is logged and the
caller proceeds as if it were a cache miss. The HTTP route is still
correct (it falls back to the underlying service), just slower.
"""

from __future__ import annotations

import logging
from typing import Any

import orjson
from redis.exceptions import RedisError

from app.core.broadcasting.pubsub import get_client

LOG = logging.getLogger(__name__)

SNAPSHOT_TTL_S = 60
HISTORY_TTL_S = 90


def _snapshot_key(symbol: str, timeframe: str) -> str:
    return f"liq:snap:{symbol}:{timeframe}"


def _history_key(
    symbol: str, timeframe: str, lookback_h: int, min_vol: float
) -> str:
    return f"liq:hist:{symbol}:{timeframe}:{lookback_h}:{int(min_vol)}"


async def get_cached_snapshot(
    symbol: str, timeframe: str
) -> dict[str, Any] | None:
    """Return the cached snapshot payload (dict) or None on miss / error."""
    try:
        raw = await get_client().get(_snapshot_key(symbol, timeframe))
        if raw is None:
            return None
        parsed: dict[str, Any] = orjson.loads(raw)
        return parsed
    except (RedisError, orjson.JSONDecodeError) as exc:
        LOG.warning(
            "liq_cache_snapshot_get_error",
            extra={"symbol": symbol, "tf": timeframe, "err": str(exc)},
        )
        return None


async def set_cached_snapshot(
    symbol: str, timeframe: str, payload: dict[str, Any]
) -> None:
    try:
        await get_client().setex(
            _snapshot_key(symbol, timeframe),
            SNAPSHOT_TTL_S,
            orjson.dumps(payload).decode(),
        )
    except RedisError as exc:
        LOG.warning(
            "liq_cache_snapshot_set_error",
            extra={"symbol": symbol, "tf": timeframe, "err": str(exc)},
        )


async def get_cached_history(
    symbol: str, timeframe: str, lookback_h: int, min_vol: float
) -> dict[str, Any] | None:
    try:
        raw = await get_client().get(
            _history_key(symbol, timeframe, lookback_h, min_vol)
        )
        if raw is None:
            return None
        parsed: dict[str, Any] = orjson.loads(raw)
        return parsed
    except (RedisError, orjson.JSONDecodeError) as exc:
        LOG.warning(
            "liq_cache_history_get_error",
            extra={"symbol": symbol, "tf": timeframe, "err": str(exc)},
        )
        return None


async def set_cached_history(
    symbol: str,
    timeframe: str,
    lookback_h: int,
    min_vol: float,
    payload: dict[str, Any],
) -> None:
    try:
        await get_client().setex(
            _history_key(symbol, timeframe, lookback_h, min_vol),
            HISTORY_TTL_S,
            orjson.dumps(payload).decode(),
        )
    except RedisError as exc:
        LOG.warning(
            "liq_cache_history_set_error",
            extra={"symbol": symbol, "tf": timeframe, "err": str(exc)},
        )
