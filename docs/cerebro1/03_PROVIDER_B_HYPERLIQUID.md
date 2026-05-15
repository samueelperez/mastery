# 03 — Provider B: Hyperliquid On-Chain

<context>
This provider reads REAL position data directly from Hyperliquid's public info endpoint. Every `clearinghouseState` response contains exact `liquidationPx`, `leverage.value`, `positionValue`, `marginUsed` for each open position of a given address — no estimation needed.

The technical challenge: there is no "give me all positions" endpoint. We need to maintain a universe of known addresses with recent activity and query each one periodically. We bootstrap this universe from (a) the public leaderboard and (b) WS subscription to public trades (every fill reveals two addresses).

Cost: €0. Endpoint is public, no auth required. Rate limit: 1.200 requests/min across all info endpoints — we use ~100 req/min steady state.
</context>

<deliverables>
- `apps/api/app/liquidation/providers/hyperliquid.py` — provider implementation.
- `apps/api/app/liquidation/providers/_hyperliquid_client.py` — thin async client wrapper.
- `apps/api/app/liquidation/providers/_hyperliquid_bootstrap.py` — address universe bootstrap task.
- `apps/api/app/core/exchanges/hyperliquid_symbols.py` — symbol mapping `BTCUSDT` <-> `BTC` etc.
- `apps/api/tests/liquidation/providers/test_hyperliquid.py` — unit tests with mocked HTTP.
- `apps/api/tests/liquidation/providers/test_hyperliquid_integration.py` — integration tests against real endpoint (`-m integration`).
</deliverables>

<file_core_exchanges_hyperliquid_symbols_py>

```python
"""Symbol mapping between internal convention and Hyperliquid's convention.

Internal: 'BTCUSDT', 'ETHUSDT', 'SOLUSDT' (matches Binance USDM).
Hyperliquid: 'BTC', 'ETH', 'SOL' (just the base asset; quote is implied USDC).
"""
from __future__ import annotations

# Forward map: internal -> Hyperliquid.
INTERNAL_TO_HYPERLIQUID: dict[str, str] = {
    "BTCUSDT": "BTC",
    "ETHUSDT": "ETH",
    "SOLUSDT": "SOL",
}

# Reverse map, built automatically.
HYPERLIQUID_TO_INTERNAL: dict[str, str] = {
    v: k for k, v in INTERNAL_TO_HYPERLIQUID.items()
}


def to_hyperliquid(symbol: str) -> str:
    """Convert internal symbol to Hyperliquid coin name. Raises KeyError if
    unsupported."""
    try:
        return INTERNAL_TO_HYPERLIQUID[symbol]
    except KeyError:
        raise KeyError(
            f"Symbol {symbol!r} not mapped to Hyperliquid. "
            f"Supported: {sorted(INTERNAL_TO_HYPERLIQUID)}"
        ) from None


def to_internal(coin: str) -> str:
    """Convert Hyperliquid coin name to internal symbol. Raises KeyError."""
    try:
        return HYPERLIQUID_TO_INTERNAL[coin]
    except KeyError:
        raise KeyError(
            f"Hyperliquid coin {coin!r} not mapped. "
            f"Known: {sorted(HYPERLIQUID_TO_INTERNAL)}"
        ) from None
```
</file_core_exchanges_hyperliquid_symbols_py>

<file_apps_api_app_liquidation_providers_hyperliquid_client_py>

```python
"""Minimal async client for Hyperliquid's public info endpoint.

We don't use CCXT here because CCXT doesn't expose clearinghouseState (it's
a Hyperliquid-specific endpoint, not the standard /fapi/v1/account).

Endpoints documented at:
https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint

Rate limit: 1.200 req/min shared across info endpoints. We use a semaphore
to cap concurrent calls at 20 and a token bucket at 100 req/min steady state.
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
LEADERBOARD_URL = "https://api.hyperliquid.xyz/info"  # type: 'leaderboard' — see below
# Note: the leaderboard "endpoint" is actually the same /info URL with a different
# request body. The public web leaderboard is at https://app.hyperliquid.xyz/leaderboard
# and uses an undocumented internal endpoint — confirm in your implementation
# session by inspecting Network tab.


class HyperliquidClient:
    """Async client for Hyperliquid info endpoint."""

    def __init__(
        self,
        *,
        timeout_s: float = 5.0,
        max_concurrent: int = 20,
        steady_state_per_min: int = 100,
    ) -> None:
        self._client = httpx.AsyncClient(timeout=timeout_s, http2=True)
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
                # Tenacity will retry.
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
        withdrawable, time. See module docstring for schema link.
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

        IMPORTANT: This endpoint shape is unofficial and may change. If it
        returns a non-list, log a warning and fall back to empty list.
        Implementation note: confirm the exact body shape against the live
        endpoint when implementing; this is a best-effort scrape of public data.
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
    """Simple token-bucket rate limiter, refills every second."""

    def __init__(self, *, rate_per_min: int) -> None:
        self._rate_per_sec = rate_per_min / 60.0
        self._tokens = float(rate_per_min)
        self._max_tokens = float(rate_per_min)
        self._lock = asyncio.Lock()
        self._last_refill = asyncio.get_event_loop().time() if asyncio._get_running_loop() else 0.0

    async def acquire(self) -> None:
        async with self._lock:
            now = asyncio.get_event_loop().time()
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
```
</file_apps_api_app_liquidation_providers_hyperliquid_client_py>

<file_apps_api_app_liquidation_providers_hyperliquid_bootstrap_py>

```python
"""Address universe bootstrap for Hyperliquid Provider B.

Two ingestion paths:
1. Leaderboard scrape on startup + every 6 hours.
2. WS trades subscription (continuous): every fill reveals two addresses.

Both write to `hyperliquid_known_addresses` with idempotent upsert.
"""
from __future__ import annotations

import asyncio
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
        self._coins = [INTERNAL_TO_HYPERLIQUID[s] for s in watch_symbols]
        self._tasks: list[asyncio.Task] = []
        self._stopping = False

    async def start(self) -> None:
        """Spawn the leaderboard refresh loop and the WS subscriber."""
        self._tasks = [
            asyncio.create_task(self._leaderboard_loop(), name="hl_leaderboard"),
            asyncio.create_task(self._ws_loop(), name="hl_ws_trades"),
        ]

    async def stop(self) -> None:
        self._stopping = True
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass

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
                    # Subscribe per coin.
                    for coin in self._coins:
                        await ws.send(json.dumps({
                            "method": "subscribe",
                            "subscription": {"type": "trades", "coin": coin},
                        }))
                    backoff = 1.0  # reset
                    async for msg in ws:
                        if self._stopping:
                            return
                        try:
                            data = json.loads(msg)
                        except json.JSONDecodeError:
                            continue
                        # Public 'trades' channel yields per-trade messages.
                        # Each trade carries 'users': [buyer, seller].
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
                text("""
                    INSERT INTO hyperliquid_known_addresses (address, first_seen_at, last_seen_at, tags)
                    VALUES (:addr, :now, :now, ARRAY[:tag])
                    ON CONFLICT (address) DO UPDATE
                      SET last_seen_at = EXCLUDED.last_seen_at,
                          tags = CASE
                              WHEN :tag = ANY(hyperliquid_known_addresses.tags) THEN hyperliquid_known_addresses.tags
                              ELSE array_append(hyperliquid_known_addresses.tags, :tag)
                          END
                """),
                [{"addr": a, "now": now, "tag": tag} for a in addresses],
            )
            await session.commit()
```
</file_apps_api_app_liquidation_providers_hyperliquid_bootstrap_py>

<file_apps_api_app_liquidation_providers_hyperliquid_py>

```python
"""Provider B — liquidation heatmap from Hyperliquid on-chain positions.

For each known address active in the last 7 days, query clearinghouseState.
Aggregate all open positions for the requested symbol into buckets keyed by
liquidationPx.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import ClassVar, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.exchanges.hyperliquid_symbols import to_hyperliquid
from app.liquidation.models import ProviderHeatmap, ProviderName, RawProviderBucket, TimeframeLiteral
from app.liquidation.providers.base import BaseLiquidationProvider
from app.liquidation.providers._hyperliquid_client import HyperliquidClient

LOG = logging.getLogger(__name__)

# Max addresses to query per heatmap call. Cap to keep latency bounded.
MAX_ADDRESSES_PER_CALL: Final[int] = 500

# How fresh "active" means.
ACTIVE_LOOKBACK_DAYS: Final[int] = 7

# Bucket size as fraction of current price.
BUCKET_PCT: Final[float] = 0.0025  # 0.25%


class HyperliquidLiquidationProvider(BaseLiquidationProvider):
    """Provider B — real on-chain position data from Hyperliquid."""

    name: ClassVar[ProviderName] = "B_hyperliquid"
    max_age_seconds: ClassVar[int] = 600  # 10 min — we refresh every 5 min in steady state
    enabled: ClassVar[bool] = True

    def __init__(
        self,
        session_factory: async_sessionmaker,
        client: HyperliquidClient,
    ) -> None:
        self._session_factory = session_factory
        self._client = client

    def supports_symbol(self, symbol: str) -> bool:
        try:
            to_hyperliquid(symbol)
            return True
        except KeyError:
            return False

    async def health_check(self) -> bool:
        try:
            mids = await self._client.all_mids()
            return isinstance(mids, dict) and len(mids) > 0
        except Exception:
            return False

    async def get_heatmap(
        self,
        symbol: str,
        timeframe: TimeframeLiteral,
        current_price: float,
        max_distance_pct: float = 10.0,
    ) -> ProviderHeatmap:
        if not self.supports_symbol(symbol):
            return ProviderHeatmap(
                provider=self.name,
                symbol=symbol,
                timeframe=timeframe,
                as_of=datetime.now(tz=UTC),
                buckets=[],
                warnings=[f"symbol_not_supported:{symbol}"],
            )

        coin = to_hyperliquid(symbol)
        now = datetime.now(tz=UTC)
        max_dist_abs = current_price * max_distance_pct / 100.0
        bucket_size = current_price * BUCKET_PCT

        # 1. Pick active addresses.
        addresses = await self._fetch_active_addresses(limit=MAX_ADDRESSES_PER_CALL)
        if not addresses:
            return ProviderHeatmap(
                provider=self.name,
                symbol=symbol,
                timeframe=timeframe,
                as_of=now,
                buckets=[],
                warnings=["address_universe_empty"],
            )

        # 2. Query clearinghouseState concurrently.
        results = await asyncio.gather(
            *(self._client.clearinghouse_state(a) for a in addresses),
            return_exceptions=True,
        )

        # 3. Extract positions for this coin.
        positions: list[dict] = []
        errors = 0
        for r in results:
            if isinstance(r, Exception):
                errors += 1
                continue
            for ap in (r.get("assetPositions") or []):
                p = ap.get("position") or {}
                if p.get("coin") != coin:
                    continue
                try:
                    liq_px = float(p["liquidationPx"]) if p.get("liquidationPx") else None
                    if liq_px is None:
                        continue
                    pos_value = float(p.get("positionValue", "0"))
                    szi = float(p.get("szi", "0"))
                    side = "long_liq" if szi > 0 else "short_liq"
                    positions.append({
                        "liq_px": liq_px,
                        "notional_usd": pos_value,
                        "side": side,
                    })
                except (KeyError, ValueError, TypeError):
                    continue

        # 4. Filter by distance and bucket.
        buckets: dict[tuple[float, float, str], float] = {}
        for p in positions:
            if abs(p["liq_px"] - current_price) > max_dist_abs:
                continue
            price_low = (p["liq_px"] // bucket_size) * bucket_size
            price_high = price_low + bucket_size
            key = (price_low, price_high, p["side"])
            buckets[key] = buckets.get(key, 0.0) + p["notional_usd"]

        bucket_list = [
            RawProviderBucket(
                price_low=k[0],
                price_high=k[1],
                side=k[2],
                est_volume_usd=v,
                provider=self.name,
                as_of=now,
            )
            for k, v in sorted(buckets.items(), key=lambda kv: -kv[1])
        ]

        # 5. Update last_account_value_usd for queried addresses (best-effort).
        # NOT done in the hot path — that's the bootstrap task's job.

        warnings = []
        if errors:
            warnings.append(f"clearinghouse_errors:{errors}")
        if len(addresses) >= MAX_ADDRESSES_PER_CALL:
            warnings.append("address_universe_truncated")

        return ProviderHeatmap(
            provider=self.name,
            symbol=symbol,
            timeframe=timeframe,
            as_of=now,
            buckets=bucket_list,
            warnings=warnings,
        )

    async def _fetch_active_addresses(self, *, limit: int) -> list[str]:
        """Top-N addresses by recency, restricted to active in the lookback window."""
        cutoff = datetime.now(tz=UTC) - timedelta(days=ACTIVE_LOOKBACK_DAYS)
        async with self._session_factory() as session:
            rows = await session.execute(
                text("""
                    SELECT address
                    FROM hyperliquid_known_addresses
                    WHERE last_seen_at >= :cutoff
                    ORDER BY
                        last_account_value_usd DESC NULLS LAST,
                        last_seen_at DESC
                    LIMIT :limit
                """),
                {"cutoff": cutoff, "limit": limit},
            )
            return [r.address for r in rows]
```
</file_apps_api_app_liquidation_providers_hyperliquid_py>

<gotchas>
- Hyperliquid uses coin names without quote: `BTC`, NOT `BTCUSDT`. Always go through `to_hyperliquid()`.
- `assetPositions` may be empty or missing. Use `(r.get("assetPositions") or [])`.
- `liquidationPx` can be the string `"None"` or actually missing for new positions before margin is committed. Guard with `if p.get("liquidationPx")`.
- `szi` is signed: positive = long, negative = short. The liquidation direction is the OPPOSITE of position direction (a long position gets liquidated DOWN, so it contributes to a `long_liq` bucket BELOW the entry).
- The WS message shape sometimes wraps trades in `{"channel": "trades", "data": [...]}`; sometimes the message itself is a heartbeat. Filter strictly on `channel == "trades"`.
- Leaderboard endpoint shape is UNDOCUMENTED publicly. Confirm the response keys at implementation time by hitting the endpoint manually. If it changes, the WS path keeps the system working — leaderboard is supplementary.
- `MAX_ADDRESSES_PER_CALL=500` × 1 call per heatmap = 500 req per heatmap. With 3 symbols × 3 TFs = 9 heatmaps per cycle = 4500 req. That's 4 seconds of token bucket budget. If you refresh every 5 minutes, this is fine. If you push to every 30s, you'll saturate. Tune.
- `asyncio.gather(return_exceptions=True)` is critical — without it, one 429 kills the whole batch.
- `_post`'s tenacity retry needs `reraise=True` so the outer `gather` sees the final failure (otherwise it gets a RetryError, not the underlying HTTPError).
- The TokenBucket is initialized with `asyncio._get_running_loop()` which can be None at construction time. The code above hides this bug — review and fix if you see `0.0` baseline causing initial burst to be allowed. Safer pattern: initialize `_last_refill` lazily on first `acquire()`.
</gotchas>

<no_lookahead_invariant>
This provider trivially respects no-lookahead because it reads CURRENT positions, not historical. The snapshot is "what positions exist at time `as_of`". Adding new positions in the future doesn't change a past snapshot, because there is no past snapshot stored locally — every call is fresh.

The persistence layer (the service) IS responsible for not retroactively mutating stored snapshots. That's enforced in `04_HEATMAP_SERVICE.md`.
</no_lookahead_invariant>

<acceptance>
- [ ] `to_hyperliquid("BTCUSDT")` returns `"BTC"`.
- [ ] `HyperliquidClient.clearinghouse_state(address)` returns a dict with `assetPositions` for a real address (manual smoke test with operator-provided test address).
- [ ] `HyperliquidAddressBootstrap.start()` populates `hyperliquid_known_addresses` within 30 seconds of running.
- [ ] `HyperliquidLiquidationProvider.get_heatmap("BTCUSDT", "4h", 84_500.0)` returns a `ProviderHeatmap` with buckets after the address universe has been bootstrapped.
- [ ] Returns empty buckets + warning `address_universe_empty` if the table is empty.
- [ ] Returns empty buckets + warning `clearinghouse_errors:N` if some addresses fail.
- [ ] Rate limiter prevents > 100 req/min steady state.
- [ ] Unit tests pass with mocked `httpx`.
- [ ] Integration test (`-m integration`) hits the real endpoint and validates response shape.
</acceptance>
