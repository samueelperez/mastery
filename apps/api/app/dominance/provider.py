"""CoinGecko dominance provider with Redis cache + auto-populating history.

Fetches current global market cap distribution from CoinGecko `/api/v3/global`
and persists every snapshot to a Redis sorted set keyed by unix timestamp.
Multi-day trends (24h, 7d) are computed by reading the snapshot in history
closest to the target offset.

History is **auto-populated by usage** — every call writes a snapshot. The
first call after N hours of inactivity has no history and reports
`trend=indeterminate`; subsequent calls bootstrap it. A future periodic
task can pre-populate but isn't required for correctness.

Caching:
- `dominance:latest` — JSON of the most recent snapshot (TTL configurable).
- `dominance:history` — ZSET keyed by `unix_ts`, value = JSON snapshot,
  trimmed to 30 days on every write.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

import httpx
import structlog
from pydantic import BaseModel

from app.broadcasting.pubsub import get_client
from app.config import get_settings

log = structlog.get_logger(__name__)

_CACHE_LATEST_KEY = "dominance:latest"
_HISTORY_KEY = "dominance:history"
_HISTORY_TRIM_SECONDS = 30 * 86400  # 30 days


# -----------------------------------------------------------------------------
# Models
# -----------------------------------------------------------------------------


class DominanceSnapshot(BaseModel):
    """A single point-in-time market-cap distribution observation."""

    btc_dominance_pct: float
    eth_dominance_pct: float
    # Everything that isn't BTC or ETH. Proxy for the "Total3" concept used
    # in TradingView (which is technically Total - BTC - ETH market cap; we
    # express it as a share of the total here).
    total3_share_pct: float
    total_market_cap_usd: float
    fetched_at: datetime


TrendDirection = Literal["up", "down", "flat", "indeterminate"]


class DominanceTrend(BaseModel):
    """Direction + magnitude of the change in BTC.D over a time window."""

    direction: TrendDirection
    # Change in percentage points (e.g. +0.8 means dominance went from 52.0 → 52.8).
    delta_pct: float


RegimeLabel = Literal["btc_season", "alt_season", "mixed", "range"]


# -----------------------------------------------------------------------------
# Pure helpers (testable without network/Redis)
# -----------------------------------------------------------------------------


def parse_coingecko_global(payload: dict[str, Any]) -> DominanceSnapshot:
    """Parse the response shape from ``/api/v3/global``.

    Raises ``ValueError`` when the shape we depend on is missing — callers
    typically catch and downgrade to a warning rather than failing the chat.
    """
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("CoinGecko /global response missing 'data'")
    cap_pct = data.get("market_cap_percentage")
    cap_usd = data.get("total_market_cap")
    if not isinstance(cap_pct, dict) or not isinstance(cap_usd, dict):
        raise ValueError(
            "CoinGecko /global response missing market_cap_percentage or total_market_cap"
        )
    btc_raw = cap_pct.get("btc")
    eth_raw = cap_pct.get("eth")
    total_raw = cap_usd.get("usd")
    if not isinstance(btc_raw, (int, float)) or not isinstance(eth_raw, (int, float)):
        raise ValueError("CoinGecko /global: btc/eth percentages missing or non-numeric")
    btc = float(btc_raw)
    eth = float(eth_raw)
    total_usd = float(total_raw) if isinstance(total_raw, (int, float)) else 0.0
    other = max(0.0, 100.0 - btc - eth)
    return DominanceSnapshot(
        btc_dominance_pct=btc,
        eth_dominance_pct=eth,
        total3_share_pct=other,
        total_market_cap_usd=total_usd,
        fetched_at=datetime.now(tz=UTC),
    )


def classify_trend(
    current_pct: float,
    prior_pct: float | None,
    *,
    flat_threshold: float = 0.5,
) -> DominanceTrend:
    """Compare current dominance % to a prior observation.

    ``flat_threshold`` is in percentage points. Default 0.5pp — dominance
    moves slowly, so a smaller change is just noise (and would generate
    spurious "up/down" signals from CoinGecko jitter).
    """
    if prior_pct is None:
        return DominanceTrend(direction="indeterminate", delta_pct=0.0)
    delta = current_pct - prior_pct
    if abs(delta) < flat_threshold:
        return DominanceTrend(direction="flat", delta_pct=delta)
    return DominanceTrend(
        direction="up" if delta > 0 else "down",
        delta_pct=delta,
    )


def classify_regime(
    *,
    btc_dominance_pct: float,
    btc_trend_1d: TrendDirection,
    btc_trend_7d: TrendDirection,
) -> RegimeLabel:
    """Pick a regime label from level + 7d trend.

    Rules of thumb the crypto community uses (acknowledging the thresholds
    are conventional, not measured):

    - ``btc_season``: BTC.D > 53 AND 7d trend is up/flat — capital
      consolidating in BTC at the expense of alts.
    - ``alt_season``: BTC.D < 47 AND 7d trend is down — capital rotating
      out of BTC into alts.
    - ``range``: 7d trend is flat OR indeterminate — no clear rotation.
    - ``mixed``: anything else (e.g. level in 47–53 band, or signals conflict).
    """
    if btc_trend_7d in ("flat", "indeterminate"):
        return "range"
    if btc_dominance_pct > 53.0 and btc_trend_7d in ("up", "flat"):
        return "btc_season"
    if btc_dominance_pct < 47.0 and btc_trend_7d == "down":
        return "alt_season"
    return "mixed"


# -----------------------------------------------------------------------------
# I/O (httpx + Redis)
# -----------------------------------------------------------------------------


async def fetch_global_snapshot_live() -> DominanceSnapshot:
    """Hit CoinGecko /api/v3/global directly. No cache lookup.

    Raises on HTTP errors / parse failures. Callers should wrap in try/except
    and degrade gracefully (the chat shouldn't fail because CoinGecko is
    flaky).
    """
    settings = get_settings()
    base = settings.coingecko_base_url.rstrip("/")
    headers: dict[str, str] = {"Accept": "application/json"}
    if settings.coingecko_api_key:
        # CoinGecko's demo plan uses x-cg-demo-api-key; pro uses x-cg-pro-api-key.
        # Demo header works for both tiers when a key is supplied to free.
        headers["x-cg-demo-api-key"] = settings.coingecko_api_key
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{base}/api/v3/global", headers=headers)
        resp.raise_for_status()
        return parse_coingecko_global(resp.json())


async def _persist_history(snap: DominanceSnapshot) -> None:
    """Append snapshot to history ZSET and trim entries older than 30 days."""
    client = get_client()
    score = int(snap.fetched_at.timestamp())
    payload = snap.model_dump_json()
    cutoff = score - _HISTORY_TRIM_SECONDS
    pipe = client.pipeline()
    pipe.zadd(_HISTORY_KEY, {payload: score})
    pipe.zremrangebyscore(_HISTORY_KEY, 0, cutoff)
    await pipe.execute()


async def _load_cached_latest() -> DominanceSnapshot | None:
    client = get_client()
    raw = await client.get(_CACHE_LATEST_KEY)
    if not raw:
        return None
    try:
        return DominanceSnapshot.model_validate_json(raw)
    except Exception:
        return None


async def _store_cached_latest(snap: DominanceSnapshot, ttl_seconds: int) -> None:
    client = get_client()
    await client.set(
        _CACHE_LATEST_KEY,
        snap.model_dump_json(),
        ex=ttl_seconds,
    )


async def _load_history_near(
    target_ts: datetime, *, window_seconds: int
) -> DominanceSnapshot | None:
    """Return the persisted snapshot closest to ``target_ts`` within
    ``±window_seconds``. None if nothing in band."""
    client = get_client()
    target_score = int(target_ts.timestamp())
    lo = target_score - window_seconds
    hi = target_score + window_seconds
    raws = await client.zrangebyscore(_HISTORY_KEY, lo, hi)
    if not raws:
        return None
    best: DominanceSnapshot | None = None
    best_dist = float("inf")
    for raw in raws:
        try:
            snap = DominanceSnapshot.model_validate_json(raw)
        except Exception:
            continue
        d = abs(snap.fetched_at.timestamp() - target_score)
        if d < best_dist:
            best_dist = d
            best = snap
    return best


# -----------------------------------------------------------------------------
# Public composition
# -----------------------------------------------------------------------------


async def get_dominance_snapshot() -> DominanceSnapshot:
    """Returns the latest snapshot — cache → live fetch → persist history.

    The live fetch + history persistence happens once per cache TTL window;
    subsequent calls within the TTL hit the cached value and do not touch
    the network.
    """
    cached = await _load_cached_latest()
    if cached is not None:
        return cached
    snap = await fetch_global_snapshot_live()
    settings = get_settings()
    await _store_cached_latest(snap, settings.dominance_cache_ttl_seconds)
    await _persist_history(snap)
    return snap


async def get_dominance_history(
    target_ts: datetime, *, window_hours: int = 6
) -> DominanceSnapshot | None:
    """Public wrapper around `_load_history_near` with hours as the unit."""
    return await _load_history_near(target_ts, window_seconds=window_hours * 3600)
