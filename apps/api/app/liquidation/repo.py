"""Repository: persistence of heatmap snapshots and provider weights.

Uses raw SQL via SQLAlchemy `text()` for clarity (this module doesn't have
the volume to need ORM). All queries are scoped by `user_id` per
CLAUDE.md::critical_invariants #2.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.liquidation.models import (
    HeatmapSnapshot,
    ProviderName,
    ProviderWeight,
    RawProviderBucket,
    TimeframeLiteral,
)


class LiquidationRepo:
    """All DB access for the liquidation module."""

    def __init__(self, session: AsyncSession, user_id: str) -> None:
        self._session = session
        self._user_id = user_id

    async def persist_snapshot(
        self,
        snapshot: HeatmapSnapshot,
        raw_buckets_by_source: dict[ProviderName, list[RawProviderBucket]],
    ) -> None:
        """Persist all raw buckets (one row per source per bucket).

        We persist RAW buckets, not the aggregated snapshot. Re-aggregation is
        cheap and lets us change weights retroactively (M2 calibration job).
        """
        rows: list[dict[str, Any]] = []
        for source, buckets in raw_buckets_by_source.items():
            for b in buckets:
                rows.append(
                    {
                        "user_id": self._user_id,
                        "symbol": snapshot.symbol,
                        "timeframe": snapshot.timeframe,
                        "snapshot_ts": snapshot.as_of,
                        "price_low": b.price_low,
                        "price_high": b.price_high,
                        "side": b.side,
                        "est_volume_usd": b.est_volume_usd,
                        "source": source,
                    }
                )

        if not rows:
            return

        await self._session.execute(
            text(
                """
                INSERT INTO liquidation_buckets (
                    user_id, symbol, timeframe, snapshot_ts,
                    price_low, price_high, side, est_volume_usd, source
                )
                VALUES (
                    :user_id, :symbol, :timeframe, :snapshot_ts,
                    :price_low, :price_high, :side, :est_volume_usd, :source
                )
                """
            ),
            rows,
        )
        await self._session.commit()

    async def fetch_weights(
        self,
        symbol: str,
        timeframe: TimeframeLiteral,
    ) -> dict[ProviderName, float]:
        """Most recent computed weights for the (symbol, tf) cell.

        Returns empty dict if no weights have been computed yet (M1 default —
        service interprets this as "use uniform weights").
        """
        result = await self._session.execute(
            text(
                """
                SELECT DISTINCT ON (provider) provider, weight
                FROM liquidation_provider_weights
                WHERE symbol = :symbol AND timeframe = :timeframe
                ORDER BY provider, computed_at DESC
                """
            ),
            {"symbol": symbol, "timeframe": timeframe},
        )
        return {row.provider: float(row.weight) for row in result}

    async def save_weights(self, weights: Iterable[ProviderWeight]) -> None:
        rows = [
            {
                "symbol": w.symbol,
                "timeframe": w.timeframe,
                "provider": w.provider,
                "weight": w.weight,
                "agreement_rate": w.agreement_rate,
                "n_samples": w.n_samples,
                "computed_at": w.computed_at,
            }
            for w in weights
        ]
        if not rows:
            return
        await self._session.execute(
            text(
                """
                INSERT INTO liquidation_provider_weights (
                    symbol, timeframe, provider, weight,
                    agreement_rate, n_samples, computed_at
                )
                VALUES (
                    :symbol, :timeframe, :provider, :weight,
                    :agreement_rate, :n_samples, :computed_at
                )
                """
            ),
            rows,
        )
        await self._session.commit()

    async def fetch_agreement_log(
        self,
        *,
        since: datetime | None = None,
        symbol: str | None = None,
        timeframe: TimeframeLiteral | None = None,
    ) -> list[dict[str, Any]]:
        """Read rows from `liquidation_agreement_log`. Used by calibration."""
        clauses = ["user_id = :uid"]
        params: dict[str, Any] = {"uid": self._user_id}
        if since:
            clauses.append("logged_at >= :since")
            params["since"] = since
        if symbol:
            clauses.append("symbol = :symbol")
            params["symbol"] = symbol
        if timeframe:
            clauses.append("timeframe = :timeframe")
            params["timeframe"] = timeframe
        where = " AND ".join(clauses)
        result = await self._session.execute(
            text(
                f"""
                SELECT symbol, timeframe, proposed_zone_price, proposed_zone_side,
                       source_a_price, source_b_price, source_c_verdict,
                       delta_a_pct, delta_b_pct, logged_at
                FROM liquidation_agreement_log
                WHERE {where}
                ORDER BY logged_at DESC
                """
            ),
            params,
        )
        return [dict(r._mapping) for r in result]
