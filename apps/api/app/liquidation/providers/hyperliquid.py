"""Provider B — liquidation heatmap from Hyperliquid on-chain positions.

For each known address active in the last 7 days, query clearinghouseState.
Aggregate all open positions for the requested symbol into buckets keyed by
liquidationPx.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.exchanges.hyperliquid_symbols import to_hyperliquid
from app.liquidation.models import (
    ProviderHeatmap,
    ProviderName,
    RawProviderBucket,
    TimeframeLiteral,
)
from app.liquidation.providers._hyperliquid_client import HyperliquidClient
from app.liquidation.providers.base import BaseLiquidationProvider

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
    # 10 min — we refresh every 5 min in steady state.
    max_age_seconds: ClassVar[int] = 600
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

        results = await asyncio.gather(
            *(self._client.clearinghouse_state(a) for a in addresses),
            return_exceptions=True,
        )

        positions: list[dict[str, Any]] = []
        errors = 0
        for r in results:
            if isinstance(r, BaseException):
                errors += 1
                continue
            for ap in r.get("assetPositions") or []:
                p = ap.get("position") or {}
                if p.get("coin") != coin:
                    continue
                try:
                    liq_raw = p.get("liquidationPx")
                    if liq_raw is None or liq_raw == "None":
                        continue
                    liq_px = float(liq_raw)
                    pos_value = float(p.get("positionValue", "0"))
                    szi = float(p.get("szi", "0"))
                    side = "long_liq" if szi > 0 else "short_liq"
                    positions.append(
                        {
                            "liq_px": liq_px,
                            "notional_usd": pos_value,
                            "side": side,
                        }
                    )
                except (KeyError, ValueError, TypeError):
                    continue

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
                text(
                    """
                    SELECT address
                    FROM hyperliquid_known_addresses
                    WHERE last_seen_at >= :cutoff
                    ORDER BY
                        last_account_value_usd DESC NULLS LAST,
                        last_seen_at DESC
                    LIMIT :limit
                    """
                ),
                {"cutoff": cutoff, "limit": limit},
            )
            return [r.address for r in rows]
