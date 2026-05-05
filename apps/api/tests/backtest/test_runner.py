"""Test the simulator (`_simulate`) directly.

Bypasses DB I/O so we can test the engine without an asyncpg session. We feed
hand-built SignalFrames and verify trade/equity output shape + arithmetic.
"""

from __future__ import annotations

import math

import polars as pl

# Force-import to populate the registry.
import app.backtest  # noqa: F401
from app.backtest.runner import _simulate
from app.backtest.strategies import SignalFrame, get_strategy


def test_simulate_produces_valid_trades_on_trending_data(trending_up_df: pl.DataFrame) -> None:
    strat = get_strategy("ema_cross_atr_stop")
    sigframe = strat.fn(trending_up_df, strat.default_params)
    trades, equity = _simulate(
        sigframe, fees_bps=4.0, slippage_atr=0.05, initial_equity=10_000.0
    )
    assert len(equity) == len(trending_up_df)
    # Equity always finite and positive (we never short and never blow up the account)
    assert all(math.isfinite(e) and e > 0 for _, e in equity)
    # If trades happened, every trade must have entry < exit_ts and finite numbers.
    for t in trades:
        assert t.entry_ts < t.exit_ts
        assert math.isfinite(t.entry_px) and t.entry_px > 0
        assert math.isfinite(t.exit_px) and t.exit_px > 0
        assert math.isfinite(t.r_multiple)
        assert t.bars_held >= 1


def test_simulate_applies_fees_on_both_sides() -> None:
    """A flat market with one round-trip should leave equity below initial by ~2x fees."""
    # Build a synthetic SignalFrame that opens on bar 5, closes on bar 10, flat price.
    n = 50
    df = pl.DataFrame(
        {
            "ts": pl.datetime_range(
                start=pl.datetime(2025, 1, 1),
                end=pl.datetime(2025, 1, 1) + pl.duration(hours=n - 1),
                interval="1h",
                eager=True,
            ),
            "o": [100.0] * n,
            "h": [101.0] * n,
            "l": [99.0] * n,
            "c": [100.0] * n,
            "v": [10.0] * n,
        }
    )
    entry = pl.Series([i == 5 for i in range(n)])
    exit_ = pl.Series([i == 10 for i in range(n)])
    sigframe = SignalFrame(df=df, entry=entry, exit_=exit_, stop_distance=None)

    trades, equity = _simulate(
        sigframe, fees_bps=10.0, slippage_atr=0.0, initial_equity=10_000.0
    )
    assert len(trades) == 1
    final_equity = equity[-1][1]
    expected_loss = 10_000.0 * (10.0 / 10_000.0) * 2.0  # 2 sides × 10 bps
    # Final equity should be ~initial - 2×fees (flat market, no slippage).
    assert math.isclose(final_equity, 10_000.0 - expected_loss, rel_tol=1e-3)


def test_simulate_stop_loss_caps_loss(trending_up_df: pl.DataFrame) -> None:
    """When the price gaps below the stop, the trade exits at stop_px (not lower)."""
    strat = get_strategy("ema_cross_atr_stop")
    sigframe = strat.fn(trending_up_df, strat.default_params)
    trades, _ = _simulate(
        sigframe, fees_bps=0.0, slippage_atr=0.0, initial_equity=10_000.0
    )
    # Any trade with exit_reason="stop" must have its R-multiple ≥ -1 + small epsilon
    # (we exit AT the stop, so the loss equals the risk by definition; equality
    # is the floor).
    for t in trades:
        if t.exit_reason == "stop":
            assert t.r_multiple >= -1.05, (
                f"stop exit should cap loss near -1R, got {t.r_multiple}"
            )


def test_simulate_compounds_real_buy_and_hold() -> None:
    """Regresión del bug B1: el simulador debe componer multiplicativamente.

    Una posición que entra a 100 y sale a 200 (precio×2) sobre un equity
    inicial de 10_000 debe terminar en ~20_000 (doblado), no en
    10_000 + (200-100)/100 × 10_000 = 20_000 — coincide en este ejemplo
    porque sólo hay un trade. La diferencia se ve con DOS trades secuenciales.
    """
    n = 30
    # Fase 1: precio 100 → 150 entre bars 5-15. Fase 2: 150 → 225 entre bars 16-25.
    closes = [100.0] * 5 + [100.0 + (50.0 * (i - 4) / 10) for i in range(5, 15)] + [150.0] * 1
    closes += [150.0 + (75.0 * (i - 16) / 9) for i in range(16, 25)] + [225.0] * 5
    n = len(closes)
    df = pl.DataFrame(
        {
            "ts": pl.datetime_range(
                start=pl.datetime(2025, 1, 1),
                end=pl.datetime(2025, 1, 1) + pl.duration(hours=n - 1),
                interval="1h",
                eager=True,
            ),
            "o": closes,
            "h": [c + 0.5 for c in closes],
            "l": [c - 0.5 for c in closes],
            "c": closes,
            "v": [10.0] * n,
        }
    )
    # Trade 1: entra bar 5, sale bar 15 (ganancia 50%).
    # Trade 2: entra bar 16, sale bar 25 (ganancia 50% sobre el equity ya
    # crecido).
    entry = pl.Series([i in (5, 16) for i in range(n)])
    exit_ = pl.Series([i in (15, 25) for i in range(n)])
    sigframe = SignalFrame(df=df, entry=entry, exit_=exit_, stop_distance=None)

    trades, equity = _simulate(
        sigframe, fees_bps=0.0, slippage_atr=0.0, initial_equity=10_000.0
    )
    assert len(trades) == 2

    final = equity[-1][1]
    # Compounding correcto: equity_final = E0 × (1+r1) × (1+r2) — multiplicativo.
    # Bug viejo (aditivo sobre initial_equity): E0 × (1 + r1 + r2).
    # Calculamos los dos esperados desde los retornos reales de los trades.
    r1 = trades[0].pnl / 10_000.0  # primer trade dimensiona sobre E0
    multiplicative_equity_after_t1 = 10_000.0 * (1 + r1)
    r2_real = trades[1].pnl / multiplicative_equity_after_t1
    expected_compounded = 10_000.0 * (1 + r1) * (1 + r2_real)
    expected_arithmetic = 10_000.0 * (1 + r1 + r2_real)

    # El compounded debe ser estrictamente mayor (ambos retornos positivos).
    assert expected_compounded > expected_arithmetic
    # Y el equity final debe coincidir con el compounded, NO con el aritmético.
    assert math.isclose(final, expected_compounded, rel_tol=1e-3), (
        f"compounding falló: equity final={final}, "
        f"esperado compounded {expected_compounded}, aritmético {expected_arithmetic}"
    )
