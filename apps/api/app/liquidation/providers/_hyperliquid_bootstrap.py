"""Address universe bootstrap for Hyperliquid Provider B.

Two ingestion paths:
1. Leaderboard scrape on startup + every 6 hours.
2. WS trades subscription (continuous): every fill reveals two addresses.

Both write to `hyperliquid_known_addresses` with idempotent upsert.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime

import websockets
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.exchanges.hyperliquid_symbols import INTERNAL_TO_HYPERLIQUID
from app.liquidation.providers._hyperliquid_client import HyperliquidClient

LOG = logging.getLogger(__name__)

WS_URL = "wss://api.hyperliquid.xyz/ws"


class HyperliquidAddressBootstrap:
    """Maintain the address universe for Provider B."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker,
        client: HyperliquidClient,
        watch_symbols: list[str],
    ) -> None:
        self._session_factory = session_factory
        self._client = client
        # Map internal symbols to Hyperliquid coins for WS subscription.
        # Silently skip symbols we don't map (e.g. BNBUSDT — not on HL).
        self._coins = [
            INTERNAL_TO_HYPERLIQUID[s] for s in watch_symbols if s in INTERNAL_TO_HYPERLIQUID
        ]
        self._tasks: list[asyncio.Task] = []
        self._stopping = False

    async def start(self) -> None:
        """Spawn the leaderboard refresh loop and the WS subscriber."""
        if not self._coins:
            LOG.info("hl_bootstrap_skip_no_supported_coins")
            return
        self._tasks = [
            asyncio.create_task(self._leaderboard_loop(), name="hl_leaderboard"),
            asyncio.create_task(self._ws_loop(), name="hl_ws_trades"),
        ]
        LOG.info("hl_bootstrap_started", extra={"coins": self._coins})

    async def stop(self) -> None:
        self._stopping = True
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self._tasks.clear()

    async def _leaderboard_loop(self) -> None:
        """Refresh the leaderboard every 6 hours; tag those addresses."""
        while not self._stopping:
            try:
                rows = await self._client.leaderboard()
                addresses = [r.get("ethAddress") for r in rows if r.get("ethAddress")]
                if addresses:
                    await self._upsert_addresses(addresses, tag="leaderboard")
                    LOG.info("hl_leaderboard_synced", extra={"n": len(addresses)})
            except Exception:
                LOG.exception("hl_leaderboard_error")
            await asyncio.sleep(6 * 3600)

    async def _ws_loop(self) -> None:
        """Subscribe to public trades for all watch coins; capture addresses
        from every fill."""
        backoff = 1.0
        while not self._stopping:
            try:
                async with websockets.connect(WS_URL, ping_interval=20) as ws:
                    for coin in self._coins:
                        await ws.send(
                            json.dumps(
                                {
                                    "method": "subscribe",
                                    "subscription": {
                                        "type": "trades",
                                        "coin": coin,
                                    },
                                }
                            )
                        )
                    backoff = 1.0  # reset
                    async for msg in ws:
                        if self._stopping:
                            return
                        try:
                            data = json.loads(msg)
                        except json.JSONDecodeError:
                            continue
                        if data.get("channel") != "trades":
                            continue
                        trades = data.get("data", [])
                        addresses: set[str] = set()
                        for t in trades:
                            for addr in t.get("users", []):
                                if isinstance(addr, str) and addr.startswith("0x"):
                                    addresses.add(addr)
                        if addresses:
                            await self._upsert_addresses(list(addresses), tag="recent_fill")
            except (websockets.WebSocketException, OSError):
                LOG.warning("hl_ws_disconnected", extra={"backoff": backoff})
                await asyncio.sleep(min(backoff, 60.0))
                backoff = min(backoff * 2, 60.0)
            except Exception:
                LOG.exception("hl_ws_error")
                await asyncio.sleep(5.0)

    async def _upsert_addresses(self, addresses: list[str], *, tag: str) -> None:
        """Idempotent insert of addresses with a tag. Updates last_seen_at."""
        now = datetime.now(tz=UTC)
        async with self._session_factory() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO hyperliquid_known_addresses
                        (address, first_seen_at, last_seen_at, tags)
                    VALUES
                        (:addr, :now, :now, ARRAY[:tag])
                    ON CONFLICT (address) DO UPDATE
                      SET last_seen_at = EXCLUDED.last_seen_at,
                          tags = CASE
                              WHEN :tag = ANY(hyperliquid_known_addresses.tags)
                                  THEN hyperliquid_known_addresses.tags
                              ELSE array_append(
                                  hyperliquid_known_addresses.tags, :tag
                              )
                          END
                    """
                ),
                [{"addr": a, "now": now, "tag": tag} for a in addresses],
            )
            await session.commit()
