"""Tests for the liquidation HTTP routes (HM-PR1).

DB-backed integration tests live elsewhere; this file focuses on the pure
helpers and on the FastAPI route wiring with mocked dependencies. The
canonical happy-path queries (`_build_latest_snapshot_payload`,
`_build_history_payload`) are covered indirectly via the cache + ETag
behavior.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("OPENROUTER_API_KEY", "sk-test-placeholder")

from app.liquidation.routes import (
    _apply_cache_headers,
    _build_history_payload,
    _build_latest_snapshot_payload,
    _etag_for,
    _zone_confidence,
)

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_zone_confidence_high_when_multiple_providers():
    out = _zone_confidence({"A_derived": 1_000_000.0, "B_hyperliquid": 500_000.0})
    assert out == "high"


def test_zone_confidence_medium_when_single_provider():
    out = _zone_confidence({"A_derived": 1_000_000.0})
    assert out == "medium"


def test_zone_confidence_ignores_zero_contributions():
    out = _zone_confidence({"A_derived": 1_000_000.0, "B_hyperliquid": 0.0})
    assert out == "medium"


def test_zone_confidence_low_when_empty():
    out = _zone_confidence({})
    assert out == "low"


def test_etag_stable_for_same_seed():
    seed = [("2026-05-13T10:00:00+00:00", 12), ("2026-05-13T10:02:00+00:00", 14)]
    assert _etag_for(seed) == _etag_for(seed)


def test_etag_differs_for_different_seeds():
    a = _etag_for([("2026-05-13T10:00:00+00:00", 12)])
    b = _etag_for([("2026-05-13T10:02:00+00:00", 12)])
    assert a != b


def test_etag_weak_format():
    out = _etag_for([("ts", 1)])
    assert out.startswith('W/"liq-')
    assert out.endswith('"')


def test_apply_cache_headers_sets_etag_and_cache_control():
    response = MagicMock()
    response.headers = {}
    _apply_cache_headers(response, 'W/"liq-abc"', 60)
    assert response.headers["ETag"] == 'W/"liq-abc"'
    cc = response.headers["Cache-Control"]
    assert "max-age=60" in cc
    assert "stale-while-revalidate=120" in cc
    assert "private" in cc


# ---------------------------------------------------------------------------
# Route builders — mocked DB
# ---------------------------------------------------------------------------


def _mock_session_yielding(rows: list[dict]) -> AsyncMock:
    """Build a mock async session whose `.execute()` returns a mappings()
    iterable yielding the given rows. Use `_mock_session_yielding_pairs`
    when two consecutive execute() calls need different return values."""
    return _mock_session_yielding_pairs([rows])


def _mock_session_yielding_pairs(rows_per_call: list[list[dict]]) -> AsyncMock:
    """Each list in `rows_per_call` is the rows the i-th `.execute()` returns."""
    session = AsyncMock()
    call_idx = 0

    async def _execute(*_args, **_kwargs):
        nonlocal call_idx
        rows = rows_per_call[call_idx] if call_idx < len(rows_per_call) else []
        call_idx += 1
        result = MagicMock()
        # `.mappings().one_or_none()` returns the first row or None.
        # `.mappings().all()` returns the list.
        result.mappings.return_value.one_or_none.return_value = (
            rows[0] if rows else None
        )
        result.mappings.return_value.all.return_value = rows
        return result

    session.execute = _execute
    return session


async def test_build_latest_snapshot_returns_none_when_empty(monkeypatch):
    import app.liquidation.routes as routes

    session = _mock_session_yielding([{"ts": None}])

    class _Scope:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *_a):
            return None

    monkeypatch.setattr(routes, "session_scope", lambda: _Scope())

    payload = await _build_latest_snapshot_payload("BTCUSDT", "4h")
    assert payload is None


async def test_build_latest_snapshot_returns_payload_when_rows(monkeypatch):
    import app.liquidation.routes as routes

    snap_ts = datetime(2026, 5, 13, 10, 0, tzinfo=UTC)
    ts_row = [{"ts": snap_ts}]
    zone_rows = [
        {
            "price_low": 80_000.0,
            "price_high": 80_200.0,
            "side": "short_liq",
            "total_volume": 5_000_000.0,
            "source_breakdown": {
                "A_derived": 3_000_000.0,
                "B_hyperliquid": 2_000_000.0,
            },
        },
    ]
    session = _mock_session_yielding_pairs([ts_row, zone_rows])

    class _Scope:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *_a):
            return None

    monkeypatch.setattr(routes, "session_scope", lambda: _Scope())

    payload = await _build_latest_snapshot_payload("BTCUSDT", "4h")
    assert payload is not None
    assert payload["symbol"] == "BTCUSDT"
    assert payload["timeframe"] == "4h"
    assert payload["as_of"] == snap_ts.isoformat()
    assert len(payload["zones"]) == 1
    zone = payload["zones"][0]
    assert zone["price_low"] == 80_000.0
    assert zone["price_high"] == 80_200.0
    assert zone["confidence"] == "high"
    assert payload["etag"].startswith('W/"liq-')


async def test_build_history_payload_groups_by_snapshot(monkeypatch):
    import app.liquidation.routes as routes

    ts1 = datetime(2026, 5, 13, 10, 0, tzinfo=UTC)
    ts2 = datetime(2026, 5, 13, 10, 2, tzinfo=UTC)
    rows = [
        {
            "snapshot_ts": ts1,
            "price_low": 80_000.0,
            "price_high": 80_200.0,
            "side": "short_liq",
            "total_volume": 3_000_000.0,
            "source_breakdown": {"A_derived": 3_000_000.0},
        },
        {
            "snapshot_ts": ts1,
            "price_low": 79_000.0,
            "price_high": 79_200.0,
            "side": "long_liq",
            "total_volume": 2_500_000.0,
            "source_breakdown": {"B_hyperliquid": 2_500_000.0},
        },
        {
            "snapshot_ts": ts2,
            "price_low": 80_100.0,
            "price_high": 80_300.0,
            "side": "short_liq",
            "total_volume": 4_000_000.0,
            "source_breakdown": {
                "A_derived": 2_000_000.0,
                "B_hyperliquid": 2_000_000.0,
            },
        },
    ]
    session = _mock_session_yielding(rows)

    class _Scope:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *_a):
            return None

    monkeypatch.setattr(routes, "session_scope", lambda: _Scope())

    payload = await _build_history_payload(
        symbol="BTCUSDT",
        timeframe="4h",
        lookback_hours=24,
        min_volume_usd=0.0,
    )
    assert payload["symbol"] == "BTCUSDT"
    assert payload["timeframe"] == "4h"
    assert payload["lookback_hours"] == 24
    assert len(payload["snapshots"]) == 2
    assert payload["snapshots"][0]["ts"] == ts1.isoformat()
    assert payload["snapshots"][1]["ts"] == ts2.isoformat()
    # Last snapshot has 2 providers → its zone gets high confidence.
    assert payload["snapshots"][1]["zones"][0]["confidence"] == "high"
    assert payload["as_of"] == ts2.isoformat()
    assert payload["etag"].startswith('W/"liq-')


async def test_build_history_payload_empty_when_no_rows(monkeypatch):
    import app.liquidation.routes as routes

    session = _mock_session_yielding([])

    class _Scope:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *_a):
            return None

    monkeypatch.setattr(routes, "session_scope", lambda: _Scope())

    payload = await _build_history_payload(
        symbol="BTCUSDT",
        timeframe="4h",
        lookback_hours=24,
        min_volume_usd=0.0,
    )
    assert payload["snapshots"] == []
    assert payload["as_of"] is None
    assert payload["etag"] == "empty"


# ---------------------------------------------------------------------------
# Scheduler smoke
# ---------------------------------------------------------------------------


async def test_scheduler_snapshot_once_no_price_skips(monkeypatch):
    """When the OHLCV table has no rows for the symbol, the cycle records
    `outcome=no_price` and does not call HeatmapService."""
    from app.liquidation.scheduler import LiquidationSnapshotScheduler

    session = _mock_session_yielding([])

    class _Scope:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *_a):
            return None

    sched = LiquidationSnapshotScheduler(
        session_factory=lambda: _Scope(),  # type: ignore[arg-type]
        hl_client=MagicMock(),
        watch_symbols=["BTCUSDT"],
    )

    # Spy on HeatmapService.get_snapshot to confirm it's NOT called.
    with patch(
        "app.liquidation.scheduler.HeatmapService"
    ) as svc_cls:
        await sched._snapshot_once("BTCUSDT", "4h")
        svc_cls.assert_not_called()


async def test_scheduler_skips_when_no_symbols():
    """start() with empty symbols is a no-op."""
    from app.liquidation.scheduler import LiquidationSnapshotScheduler

    sched = LiquidationSnapshotScheduler(
        session_factory=MagicMock(),
        hl_client=MagicMock(),
        watch_symbols=[],
    )
    await sched.start()
    assert sched._tasks == []
