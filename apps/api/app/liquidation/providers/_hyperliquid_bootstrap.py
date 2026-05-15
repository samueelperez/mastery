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
from app.core.observability.metrics import liq_active_addresses
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
        # Serialises _upsert_addresses calls across the WS loop AND the
        # leaderboard loop. Both run concurrently as separate tasks and
        # touch the same `hyperliquid_known_addresses` rows; without this
        # lock, sorting alone is not enough to prevent deadlocks at the
        # asyncpg/PG level (ON CONFLICT DO UPDATE on overlapping batches).
        self._upsert_lock = asyncio.Lock()

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
        """Idempotent insert of addresses with a tag. Updates last_seen_at.

        Addresses are deduplicated + **sorted** before the executemany so
        concurrent batches acquire row-level locks in the same order. The
        WS loop subscribes to N coins simultaneously; without the sort,
        two batches that share addresses could acquire locks in opposite
        orders and deadlock (observed under load: PG raised
        `asyncpg.exceptions.DeadlockDetectedError`).
        """
        now = datetime.now(tz=UTC)
        ordered = sorted(set(addresses))
        if not ordered:
            return
        # Retry once on deadlock — PG guarantees one of the two
        # conflicting txs has rolled back, so retry is safe. With sorted
        # batches + asyncio.Lock + retry, the deadlock window is the
        # uvicorn `--reload` boundary (old + new instances overlap for a
        # few seconds); after that, the system runs clean.
        for attempt in (0, 1):
            try:
                async with self._upsert_lock, self._session_factory() as session:
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
                        [{"addr": a, "now": now, "tag": tag} for a in ordered],
                    )
                    await session.commit()
                break
            except Exception as exc:
                # asyncpg's DeadlockDetectedError surfaces wrapped in
                # several layers (AsyncAdapt_asyncpg_dbapi.Error,
                # SQLAlchemy DBAPIError, etc.). Catch broadly and walk
                # the cause chain by string — robust to wrapping
                # changes across SQLAlchemy versions.
                if not _looks_like_deadlock(exc) or attempt > 0:
                    raise
                LOG.warning(
                    "hl_upsert_deadlock_retry",
                    extra={"n_addrs": len(ordered), "tag": tag},
                )
                await asyncio.sleep(0.05 + 0.10 * attempt)

        async with self._session_factory() as session:
            # Refresh active-addresses gauge. Single SELECT after each upsert
            # batch — cheap and gives near-real-time visibility into universe
            # growth without a separate polling loop.
            count_row = await session.execute(
                text("SELECT COUNT(*) FROM hyperliquid_known_addresses")
            )
            total = count_row.scalar_one() or 0
            liq_active_addresses.set(float(total))


def _looks_like_deadlock(exc: BaseException) -> bool:
    """Walk the exception cause chain looking for a deadlock signature.

    asyncpg + SQLAlchemy wrap the original ``asyncpg.exceptions.DeadlockDetectedError``
    in several layers (``AsyncAdapt_asyncpg_dbapi.Error`` → ``DBAPIError``
    → ``OperationalError``), and the wrapping changes between releases. A
    string-level check across the chain is the most resilient.
    """
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if "deadlock" in str(cur).lower():
            return True
        if "DeadlockDetected" in type(cur).__name__:
            return True
        cur = cur.__cause__ or cur.__context__
    return False
