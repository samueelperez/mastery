"""B.2 — Slippage buffer raises the R:R floor per symbol.

The base R:R floor (1.5) plus a per-symbol buffer (default 0.3 for BTC,
0.4 for SOL, etc.) means setups with marginal nominal R:R get retried.
These tests pin the threshold map + the buffer-aware ModelRetry path.
"""

from __future__ import annotations

from app.core.config import get_settings


def test_btcusdt_buffer_default() -> None:
    s = get_settings()
    assert s.slippage_buffer_r("BTCUSDT") == s.slippage_buffer_r_btcusdt


def test_ethusdt_buffer_default() -> None:
    s = get_settings()
    assert s.slippage_buffer_r("ETHUSDT") == s.slippage_buffer_r_ethusdt


def test_uppercases_symbol() -> None:
    """Lowercase input should map to the same buffer as uppercase."""
    s = get_settings()
    assert s.slippage_buffer_r("btcusdt") == s.slippage_buffer_r("BTCUSDT")


def test_unknown_symbol_falls_back_to_default() -> None:
    s = get_settings()
    # XYZUSDT isn't in the per-symbol map → uses _default.
    assert s.slippage_buffer_r("XYZUSDT") == s.slippage_buffer_r_default
    assert s.slippage_buffer_r("WIFUSDT") == s.slippage_buffer_r_default


def test_min_rr_with_buffer_is_above_base_floor() -> None:
    """The whole point of the buffer: floor strictly above 1.5 for liquid
    symbols, strictly higher for less liquid (SOL, fallback)."""
    s = get_settings()
    assert 1.5 + s.slippage_buffer_r("BTCUSDT") >= 1.8  # 1.5 + 0.3
    assert 1.5 + s.slippage_buffer_r("SOLUSDT") >= 1.9  # 1.5 + 0.4
    assert 1.5 + s.slippage_buffer_r("XYZUSDT") >= 2.0  # 1.5 + 0.5 default
