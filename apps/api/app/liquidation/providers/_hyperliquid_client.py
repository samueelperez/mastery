"""Minimal async client for Hyperliquid's public info endpoint.

We don't use CCXT here because CCXT doesn't expose clearinghouseState (it's
a Hyperliquid-specific endpoint, not the standard /fapi/v1/account).

Endpoints documented at:
https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint

Rate limit: 1.200 req/min shared across info endpoints. We use a semaphore
to cap concurrent calls and a token bucket at 100 req/min steady state.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

LOG = logging.getLogger(__name__)

INFO_URL = "https://api.hyperliquid.xyz/info"


class HyperliquidClient:
    """Async client for Hyperliquid info endpoint."""

    def __init__(
        self,
        *,
        timeout_s: float = 5.0,
        max_concurrent: int = 20,
        steady_state_per_min: int = 100,
    ) -> None:
        # HTTP/1.1 is sufficient for the info endpoint; HTTP/2 requires the
        # optional `h2` package and gives no measurable win for one-shot POSTs.
        self._client = httpx.AsyncClient(timeout=timeout_s)
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._token_bucket = _TokenBucket(rate_per_min=steady_state_per_min)

    async def close(self) -> None:
        await self._client.aclose()

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential_jitter(initial=0.5, max=30.0),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True,
    )
    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        await self._token_bucket.acquire()
        async with self._semaphore:
            resp = await self._client.post(INFO_URL, json=body)
            if resp.status_code == 429:
                LOG.warning("hyperliquid_rate_limited", extra={"body": body})
                raise httpx.HTTPStatusError(
                    "429 rate limit",
                    request=resp.request,
                    response=resp,
                )
            resp.raise_for_status()
            return resp.json()

    async def clearinghouse_state(self, address: str) -> dict[str, Any]:
        """Get all open perp positions for an address.

        Returns dict with assetPositions, marginSummary, crossMarginSummary,
        withdrawable, time.
        """
        return await self._post({"type": "clearinghouseState", "user": address})

    async def meta(self) -> dict[str, Any]:
        """Get the perp universe (list of supported coins + their asset indices)."""
        return await self._post({"type": "meta"})

    async def all_mids(self) -> dict[str, str]:
        """Get current mid prices for all coins. Returns dict[coin] -> str price."""
        return await self._post({"type": "allMids"})

    async def leaderboard(self) -> list[dict[str, Any]]:
        """Get the public leaderboard.

        The endpoint shape is unofficial. If it returns a non-list, log a
        warning and fall back to empty list. WS bootstrap keeps the system
        working even if leaderboard breaks.
        """
        try:
            data = await self._post({"type": "leaderboard"})
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "leaderboardRows" in data:
                return data["leaderboardRows"]
            LOG.warning(
                "hyperliquid_leaderboard_unexpected_shape",
                extra={"keys": list(data.keys()) if isinstance(data, dict) else "non-dict"},
            )
            return []
        except httpx.HTTPError:
            return []


class _TokenBucket:
    """Simple token-bucket rate limiter, refills continuously.

    Lazy init of `_last_refill` on first `acquire()` — initializing at
    construction time would call `get_event_loop().time()` before a loop
    exists when the client is built outside an async context (spec gotcha).
    """

    def __init__(self, *, rate_per_min: int) -> None:
        self._rate_per_sec = rate_per_min / 60.0
        self._max_tokens = float(rate_per_min)
        self._tokens = float(rate_per_min)
        self._lock = asyncio.Lock()
        self._last_refill: float | None = None

    async def acquire(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            if self._last_refill is None:
                self._last_refill = now
            elapsed = now - self._last_refill
            self._tokens = min(
                self._max_tokens,
                self._tokens + elapsed * self._rate_per_sec,
            )
            self._last_refill = now
            if self._tokens < 1.0:
                wait_s = (1.0 - self._tokens) / self._rate_per_sec
                await asyncio.sleep(wait_s)
                self._tokens = 0.0
            else:
                self._tokens -= 1.0
