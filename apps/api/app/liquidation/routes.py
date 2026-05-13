"""HTTP API for the liquidation heatmap.

Two endpoints powering the 2D ``tiempo × precio`` visualization on the
chart:

- ``GET /liquidation/heatmap/{symbol}/{tf}`` — most recent snapshot.
- ``GET /liquidation/heatmap/{symbol}/{tf}/history`` — time-series of
  snapshots over a configurable lookback window.

Auth
----
Both endpoints require an authenticated session via
``Depends(require_user_id)``. The underlying data is system-wide
(scheduler-generated, stored under :data:`SYSTEM_USER_ID`), so the auth
is a gate, not a per-user filter — every authenticated user sees the
same market data.

Caching
-------
Valkey TTLs of 60s (snapshot) and 90s (history) absorb the latency of
the underlying queries. ETag-based revalidation lets the frontend skip
re-rendering when nothing changed.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy import text

from app.core.auth import require_user_id
from app.core.db import session_scope
from app.liquidation.cache import (
    HISTORY_TTL_S,
    SNAPSHOT_TTL_S,
    get_cached_history,
    get_cached_snapshot,
    set_cached_history,
    set_cached_snapshot,
)
from app.liquidation.scheduler import SYSTEM_USER_ID

log = structlog.get_logger("api.liquidation")
router = APIRouter()


TimeframeLiteral = Literal["1h", "4h", "1d"]


# -----------------------------------------------------------------------------
# /heatmap/{symbol}/{tf} — most recent snapshot
# -----------------------------------------------------------------------------


@router.get(
    "/liquidation/heatmap/{symbol}/{timeframe}",
    tags=["liquidation"],
)
async def get_heatmap_snapshot(
    symbol: str,
    timeframe: TimeframeLiteral,
    response: Response,
    _user_id: Annotated[str, Depends(require_user_id)],
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> dict[str, Any]:
    """Latest snapshot reconstructed from ``liquidation_buckets``.

    Returns the aggregated zones for the most recent ``snapshot_ts`` the
    scheduler has persisted. 503 if no rows yet (deploy fresh, scheduler
    needs ~2 min to populate).
    """
    symbol = symbol.upper()
    cached = await get_cached_snapshot(symbol, timeframe)
    if cached is not None:
        etag = cached.get("etag", "")
        if if_none_match and if_none_match == etag:
            response.status_code = 304
            return {}
        _apply_cache_headers(response, etag, SNAPSHOT_TTL_S)
        return cached

    payload = await _build_latest_snapshot_payload(symbol, timeframe)
    if payload is None:
        response.headers["Retry-After"] = "120"
        raise HTTPException(
            status_code=503,
            detail=(
                "No liquidation snapshots persisted yet for "
                f"{symbol} {timeframe}. The scheduler runs every 2 min "
                "after API startup."
            ),
        )

    await set_cached_snapshot(symbol, timeframe, payload)
    etag = payload["etag"]
    if if_none_match and if_none_match == etag:
        response.status_code = 304
        return {}
    _apply_cache_headers(response, etag, SNAPSHOT_TTL_S)
    return payload


# -----------------------------------------------------------------------------
# /heatmap/{symbol}/{tf}/history — time-series
# -----------------------------------------------------------------------------


@router.get(
    "/liquidation/heatmap/{symbol}/{timeframe}/history",
    tags=["liquidation"],
)
async def get_heatmap_history(
    symbol: str,
    timeframe: TimeframeLiteral,
    response: Response,
    _user_id: Annotated[str, Depends(require_user_id)],
    lookback_hours: Annotated[int, Query(ge=1, le=168)] = 24,
    min_volume_usd: Annotated[float, Query(ge=0.0)] = 0.0,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> dict[str, Any]:
    """Time-series of snapshots from ``liquidation_buckets`` in the window.

    Response shape::

        {
          "symbol": "BTCUSDT",
          "timeframe": "4h",
          "lookback_hours": 24,
          "as_of": "2026-05-13T10:30:00Z",
          "etag": "sha256-...",
          "snapshots": [
            {"ts": "...", "zones": [{price_low, price_high, side, est_volume_usd,
                                     source_breakdown, confidence}]},
            ...
          ]
        }

    Each ``MagnetZone`` is reconstructed from the per-provider raw rows
    (a SUM + jsonb_object_agg). The aggregate volume is the sum across
    sources — calibrated weights apply later (M2).
    """
    symbol = symbol.upper()
    cached = await get_cached_history(symbol, timeframe, lookback_hours, min_volume_usd)
    if cached is not None:
        etag = cached.get("etag", "")
        if if_none_match and if_none_match == etag:
            response.status_code = 304
            return {}
        _apply_cache_headers(response, etag, HISTORY_TTL_S)
        return cached

    payload = await _build_history_payload(
        symbol=symbol,
        timeframe=timeframe,
        lookback_hours=lookback_hours,
        min_volume_usd=min_volume_usd,
    )

    await set_cached_history(symbol, timeframe, lookback_hours, min_volume_usd, payload)
    etag = payload["etag"]
    if if_none_match and if_none_match == etag:
        response.status_code = 304
        return {}
    _apply_cache_headers(response, etag, HISTORY_TTL_S)
    return payload


# -----------------------------------------------------------------------------
# Builders
# -----------------------------------------------------------------------------


async def _build_latest_snapshot_payload(
    symbol: str, timeframe: TimeframeLiteral
) -> dict[str, Any] | None:
    """Read the most recent ``snapshot_ts`` and aggregate its zones."""
    async with session_scope() as session:
        latest_ts_row = (
            await session.execute(
                text(
                    """
                    SELECT MAX(snapshot_ts) AS ts
                    FROM liquidation_buckets
                    WHERE user_id = :uid AND symbol = :symbol AND timeframe = :tf
                    """
                ),
                {"uid": SYSTEM_USER_ID, "symbol": symbol, "tf": timeframe},
            )
        ).mappings().one_or_none()
        if latest_ts_row is None or latest_ts_row["ts"] is None:
            return None

        snapshot_ts: datetime = latest_ts_row["ts"]
        rows = (
            await session.execute(
                text(
                    """
                    SELECT
                      price_low::float AS price_low,
                      price_high::float AS price_high,
                      side,
                      SUM(est_volume_usd)::float AS total_volume,
                      jsonb_object_agg(source, est_volume_usd) AS source_breakdown
                    FROM liquidation_buckets
                    WHERE user_id = :uid
                      AND symbol = :symbol
                      AND timeframe = :tf
                      AND snapshot_ts = :ts
                    GROUP BY price_low, price_high, side
                    ORDER BY price_low ASC
                    """
                ),
                {
                    "uid": SYSTEM_USER_ID,
                    "symbol": symbol,
                    "tf": timeframe,
                    "ts": snapshot_ts,
                },
            )
        ).mappings().all()

    zones = [
        {
            "price_low": r["price_low"],
            "price_high": r["price_high"],
            "side": r["side"],
            "est_volume_usd": r["total_volume"],
            "source_breakdown": dict(r["source_breakdown"] or {}),
            "confidence": _zone_confidence(r["source_breakdown"] or {}),
        }
        for r in rows
    ]
    payload = {
        "symbol": symbol,
        "timeframe": timeframe,
        "as_of": snapshot_ts.isoformat(),
        "zones": zones,
        "etag": _etag_for([(snapshot_ts.isoformat(), len(zones))]),
    }
    return payload


async def _build_history_payload(
    *,
    symbol: str,
    timeframe: TimeframeLiteral,
    lookback_hours: int,
    min_volume_usd: float,
) -> dict[str, Any]:
    """Read all snapshots in the lookback window, grouped per ``snapshot_ts``."""
    since = datetime.now(tz=UTC) - timedelta(hours=lookback_hours)
    async with session_scope() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT
                      snapshot_ts,
                      price_low::float AS price_low,
                      price_high::float AS price_high,
                      side,
                      SUM(est_volume_usd)::float AS total_volume,
                      jsonb_object_agg(source, est_volume_usd) AS source_breakdown
                    FROM liquidation_buckets
                    WHERE user_id = :uid
                      AND symbol = :symbol
                      AND timeframe = :tf
                      AND snapshot_ts >= :since
                    GROUP BY snapshot_ts, price_low, price_high, side
                    HAVING SUM(est_volume_usd) >= :min_volume
                    ORDER BY snapshot_ts ASC, price_low ASC
                    """
                ),
                {
                    "uid": SYSTEM_USER_ID,
                    "symbol": symbol,
                    "tf": timeframe,
                    "since": since,
                    "min_volume": min_volume_usd,
                },
            )
        ).mappings().all()

    snapshots_by_ts: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        snapshots_by_ts[r["snapshot_ts"]].append(
            {
                "price_low": r["price_low"],
                "price_high": r["price_high"],
                "side": r["side"],
                "est_volume_usd": r["total_volume"],
                "source_breakdown": dict(r["source_breakdown"] or {}),
                "confidence": _zone_confidence(r["source_breakdown"] or {}),
            }
        )

    snapshots: list[dict[str, Any]] = []
    etag_seed: list[tuple[str, int]] = []
    for ts, zones in sorted(snapshots_by_ts.items()):
        ts_iso = ts.isoformat()
        snapshots.append({"ts": ts_iso, "zones": zones})
        etag_seed.append((ts_iso, len(zones)))
    as_of = snapshots[-1]["ts"] if snapshots else None
    payload = {
        "symbol": symbol,
        "timeframe": timeframe,
        "lookback_hours": lookback_hours,
        "min_volume_usd": min_volume_usd,
        "as_of": as_of,
        "snapshots": snapshots,
        "etag": _etag_for(etag_seed) if etag_seed else "empty",
    }
    return payload


def _zone_confidence(source_breakdown: dict[str, float]) -> str:
    """Heuristic confidence for a reconstructed zone: ``high`` when ≥2 providers
    contributed, ``medium`` for 1. Real cross-provider agreement lives on the
    full :class:`HeatmapSnapshot`; this is a per-zone proxy good enough for the
    UI's color/border rule."""
    n = len([v for v in source_breakdown.values() if v and v > 0])
    if n >= 2:
        return "high"
    if n == 1:
        return "medium"
    return "low"


def _etag_for(seed: list[tuple[str, int]]) -> str:
    h = hashlib.sha256()
    for ts, n in seed:
        h.update(ts.encode())
        h.update(str(n).encode())
    return f'W/"liq-{h.hexdigest()[:16]}"'


def _apply_cache_headers(response: Response, etag: str, max_age: int) -> None:
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = (
        f"private, max-age={max_age}, stale-while-revalidate={max_age * 2}"
    )
