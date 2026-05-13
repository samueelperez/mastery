"""Unit tests for compute_provider_weights."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.liquidation.calibration import (
    MIN_SAMPLES,
    WEIGHT_FLOOR,
    compute_provider_weights,
)


def _log_row(
    symbol: str,
    timeframe: str,
    verdict: str,
    delta_a: float | None,
    delta_b: float | None,
    days_ago: int = 1,
) -> dict:
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "proposed_zone_price": 84_000.0,
        "proposed_zone_side": "short_liq",
        "source_a_price": 84_000.0 if delta_a is None else 84_000.0 * (1 + delta_a / 100),
        "source_b_price": 84_000.0 if delta_b is None else 84_000.0 * (1 + delta_b / 100),
        "source_c_verdict": verdict,
        "delta_a_pct": delta_a,
        "delta_b_pct": delta_b,
        "logged_at": datetime.now(tz=UTC) - timedelta(days=days_ago),
    }


async def test_empty_log_returns_empty() -> None:
    repo = AsyncMock()
    repo.fetch_agreement_log = AsyncMock(return_value=[])
    out = await compute_provider_weights(repo)
    assert out == []


async def test_skips_below_min_samples() -> None:
    repo = AsyncMock()
    repo.fetch_agreement_log = AsyncMock(
        return_value=[_log_row("BTCUSDT", "4h", "agree", 0.2, 0.4) for _ in range(MIN_SAMPLES - 1)]
    )
    out = await compute_provider_weights(repo)
    assert out == []


async def test_perfect_agreement_yields_floor_normalized() -> None:
    """Both providers always agree with TD → 1.0 raw → 0.5 each after norm."""
    repo = AsyncMock()
    repo.fetch_agreement_log = AsyncMock(
        return_value=[_log_row("BTCUSDT", "4h", "agree", 0.1, 0.2) for _ in range(MIN_SAMPLES * 2)]
    )
    out = await compute_provider_weights(repo)
    assert len(out) == 2
    weights = {w.provider: w.weight for w in out}
    assert weights["A_derived"] == pytest.approx(0.5, rel=1e-3)
    assert weights["B_hyperliquid"] == pytest.approx(0.5, rel=1e-3)


async def test_one_provider_zero_agreement_floors() -> None:
    """Provider A always far off; B always close. A → floor, B → 1.0; norm."""
    repo = AsyncMock()
    repo.fetch_agreement_log = AsyncMock(
        return_value=[_log_row("BTCUSDT", "4h", "agree", 5.0, 0.1) for _ in range(MIN_SAMPLES * 2)]
    )
    out = await compute_provider_weights(repo)
    weights = {w.provider: w.weight for w in out}
    assert weights["A_derived"] == pytest.approx(WEIGHT_FLOOR / (WEIGHT_FLOOR + 1.0), rel=0.01)
    assert weights["B_hyperliquid"] == pytest.approx(1.0 / (WEIGHT_FLOOR + 1.0), rel=0.01)


async def test_skipped_verdict_ignored() -> None:
    """Skipped entries are excluded; counts should still produce weights."""
    repo = AsyncMock()
    rows = [_log_row("BTCUSDT", "4h", "agree", 0.1, 0.1) for _ in range(MIN_SAMPLES)]
    rows.extend([_log_row("BTCUSDT", "4h", "skipped", None, None) for _ in range(50)])
    repo.fetch_agreement_log = AsyncMock(return_value=rows)
    out = await compute_provider_weights(repo)
    assert len(out) == 2


async def test_weights_sum_to_one_per_cell() -> None:
    repo = AsyncMock()
    repo.fetch_agreement_log = AsyncMock(
        return_value=[_log_row("BTCUSDT", "4h", "agree", 0.3, 1.0) for _ in range(MIN_SAMPLES * 2)]
    )
    out = await compute_provider_weights(repo)
    by_cell: dict = {}
    for w in out:
        by_cell.setdefault((w.symbol, w.timeframe), []).append(w.weight)
    for _cell, weights in by_cell.items():
        assert sum(weights) == pytest.approx(1.0, rel=1e-3)


async def test_disagree_verdict_kills_provider_rate() -> None:
    """Even if provider's price is close, TD verdict 'disagree' → 0% rate."""
    repo = AsyncMock()
    repo.fetch_agreement_log = AsyncMock(
        return_value=[
            _log_row("BTCUSDT", "4h", "disagree", 0.1, 0.1) for _ in range(MIN_SAMPLES * 2)
        ]
    )
    out = await compute_provider_weights(repo)
    # Both providers had close prices but TD always disagreed → rate=0 → floor.
    weights = {w.provider: w for w in out}
    assert weights["A_derived"].agreement_rate == pytest.approx(0.0)
    assert weights["B_hyperliquid"].agreement_rate == pytest.approx(0.0)
    # Both at floor → 50/50 after normalize.
    assert weights["A_derived"].weight == pytest.approx(0.5, rel=1e-3)
