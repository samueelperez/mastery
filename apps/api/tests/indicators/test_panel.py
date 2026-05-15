"""compute_panel: integration test for the multi-indicator entry point used by tools.

We avoid spinning up a real DB session by mocking `fetch_range` to return synthetic
candles. This keeps the test fast and deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.market.indicators.panel import IndicatorSpec, compute_panel
from app.market.ohlcv.models import OHLCV


def _fake_rows(n: int) -> list[OHLCV]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    rows: list[OHLCV] = []
    for i in range(n):
        r = OHLCV()
        r.exchange = "binance_usdm"
        r.symbol = "BTCUSDT"
        r.timeframe = "1h"
        r.ts = start + timedelta(hours=i)
        base = 100.0 + i * 0.1
        r.o = base
        r.h = base + 0.5
        r.l = base - 0.5
        r.c = base + 0.2
        r.v = 10.0
        rows.append(r)
    return rows


@pytest.mark.asyncio
async def test_compute_panel_chains_specs(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _fake_rows(200)
    fake_fetch = AsyncMock(return_value=rows)
    monkeypatch.setattr("app.market.indicators.panel.fetch_range", fake_fetch)

    df = await compute_panel(
        session=None,  # type: ignore[arg-type]
        exchange="binance_usdm",
        symbol="BTCUSDT",
        timeframe="1h",
        lookback=200,
        specs=[
            IndicatorSpec(name="ema", length=21),
            IndicatorSpec(name="ema", length=55),
            IndicatorSpec(name="rsi", length=14),
            IndicatorSpec(name="atr", length=14),
            IndicatorSpec(name="macd"),
            IndicatorSpec(name="bbands", length=20),
            IndicatorSpec(name="adx", length=14),
            IndicatorSpec(name="vwap"),
        ],
    )

    # Required base columns
    for col in ("ts", "o", "h", "l", "c", "v"):
        assert col in df.columns

    # Indicator columns
    for col in (
        "ema_21",
        "ema_55",
        "rsi_14",
        "atr_14",
        "macd",
        "macd_signal",
        "macd_hist",
        "bb_mid",
        "bb_upper",
        "bb_lower",
        "bb_bw",
        "adx",
        "plus_di",
        "minus_di",
        "vwap",
    ):
        assert col in df.columns, f"missing column {col}"

    # ts is monotonically ascending
    ts = df["ts"].to_list()
    assert all(ts[i] < ts[i + 1] for i in range(len(ts) - 1))

    # Trailing rows have non-null indicator values
    last = df.tail(1).to_dicts()[0]
    for k in ("ema_21", "ema_55", "rsi_14", "atr_14", "adx", "vwap"):
        assert last[k] is not None, f"{k} should be non-null at tail"


@pytest.mark.asyncio
async def test_compute_panel_no_collision_multiple_bbands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regresión del bug B4: pedir bbands(20) + bbands(50) en la misma llamada
    debe producir 2 sets de columnas, no una pisar a la otra."""
    rows = _fake_rows(200)
    fake_fetch = AsyncMock(return_value=rows)
    monkeypatch.setattr("app.market.indicators.panel.fetch_range", fake_fetch)

    df = await compute_panel(
        session=None,  # type: ignore[arg-type]
        exchange="binance_usdm",
        symbol="BTCUSDT",
        timeframe="1h",
        lookback=200,
        specs=[
            IndicatorSpec(name="bbands", length=20),  # default → bb_mid
            IndicatorSpec(name="bbands", length=50),  # custom → bb_mid_50
        ],
    )
    # Las 4 columnas del default + las 4 del suffix deben coexistir
    for col in ("bb_mid", "bb_upper", "bb_lower", "bb_bw"):
        assert col in df.columns, f"missing default column {col}"
    for col in ("bb_mid_50", "bb_upper_50", "bb_lower_50", "bb_bw_50"):
        assert col in df.columns, f"missing custom column {col}"
    # Y deben tener valores distintos (mid de 20 != mid de 50 en cualquier
    # ventana no degenerada).
    last20 = df["bb_mid"][-1]
    last50 = df["bb_mid_50"][-1]
    assert last20 is not None and last50 is not None
    assert last20 != last50


@pytest.mark.asyncio
async def test_compute_panel_empty_returns_empty_df(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.market.indicators.panel.fetch_range", AsyncMock(return_value=[]))
    df = await compute_panel(
        session=None,  # type: ignore[arg-type]
        exchange="binance_usdm",
        symbol="ZZZUSDT",
        timeframe="1h",
        lookback=10,
        specs=[IndicatorSpec(name="ema", length=21)],
    )
    assert df.height == 0
    assert "c" in df.columns
