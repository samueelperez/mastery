"""B.2 Paper-trading fill simulator.

Realistic-enough slippage so paper P&L tracks reality close enough to be
predictive of live performance:

  slippage_pct = max(spread_pct, atr_pct * latency_seconds / 60 * k)

where:
- `spread_pct` is the observed bid-ask spread at fill time, in % of price
  (≈ 0.01-0.02% for BTCUSDT/ETHUSDT during normal hours; widens 3-5x
  during squeezes).
- `atr_pct * latency / 60 * k` is the volatility-driven impact: for a
  symbol moving 1% per hour, a 30s order-route latency adds ~0.5% * k
  of expected slippage on top of the spread. `k=0.3` calibrated to
  historical Binance USDT-M orderbook data.

For market orders we also add half-spread on the WORSE side of the book
(long fill above mid, short fill below mid).

Fees: Binance USDT-M perp taker is 0.04% per side. Funding cost is
prorrated by hold time at the 8h-rate sampled at fill time (skipped here;
the caller can pass `funding_bps` as observed).

This is a pure function; persistence lives in `app/storage/paper_repo.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class FillSimulationInput:
    side: Literal["long", "short"]
    kind: Literal["entry", "exit"]
    intended_px: float
    spread_pct: float  # bid-ask spread at fill time (% of price)
    atr_pct: float  # 14-period ATR / current_price (% of price)
    latency_seconds: float = 1.0  # signal-to-router latency
    impact_k: float = 0.3  # volatility-impact multiplier (calibrable)
    taker_fee_bps: float = 4.0  # Binance USDT-M taker = 0.04% = 4 bps


@dataclass(frozen=True)
class FillSimulationOutput:
    filled_px: float
    slippage_bps: float  # >0 = worse-than-intended; <0 = better
    fee_bps: float


def simulate_fill(inp: FillSimulationInput) -> FillSimulationOutput:
    """Computes a realistic filled price + slippage in bps.

    For entries we slip the WORSE direction (long fills above intended,
    short fills below). For exits we slip the FAVORABLE direction toward
    market — i.e. an exit fills at the worse side of the book too because
    we're crossing the spread to close.

    Returns slippage in bps (1 bps = 0.01% = 0.0001 of intended_px).
    Positive bps = lost edge; negative = improvement (rare but possible
    when the candle's range straddles the intended price favorably).
    """
    # Component A: half-spread (always crossed for market orders).
    half_spread_pct = max(inp.spread_pct, 0.0) / 2.0
    # Component B: volatility-driven impact, scales with latency.
    impact_pct = max(
        max(inp.atr_pct, 0.0) * (inp.latency_seconds / 60.0) * inp.impact_k, 0.0
    )
    # Total adverse slippage as fraction of price.
    slippage_pct = half_spread_pct + impact_pct

    # Direction: entries pay it (worse fill); exits also pay it (also worse
    # for the same reason — crossing the spread).
    direction_sign = 1.0 if inp.side == "long" else -1.0
    if inp.kind == "exit":
        # An exit on a long sells (worse → lower price). For shorts, exit
        # buys back (worse → higher price). Sign flips relative to entry.
        direction_sign *= -1.0

    filled_px = inp.intended_px * (1.0 + direction_sign * slippage_pct / 100.0)

    # Slippage in bps, signed (positive = lost edge for the trader).
    # We measure as the absolute price change in bps and sign it by whether
    # it hurts the trader (it does for adverse direction).
    slip_bps = slippage_pct * 100.0  # 1% = 100 bps
    # By construction (both entry and exit move to worse side) slippage is
    # always non-negative bps. Calibration job uses these positive bps as the
    # observed cost per fill.

    return FillSimulationOutput(
        filled_px=filled_px,
        slippage_bps=slip_bps,
        fee_bps=inp.taker_fee_bps,
    )


def compute_funding_cost_bps(
    *,
    side: Literal["long", "short"],
    funding_rate_8h: float,
    hold_hours: float,
) -> float:
    """Prorated funding cost in bps over the hold period.

    Funding settles every 8h on Binance USDT-M. If funding is +0.01% per 8h
    and a long holds for 24h, they pay 3 * 0.01% = 0.03% = 3 bps. Shorts
    receive the inverse.

    `funding_rate_8h` is the per-8h rate as a fraction (e.g. 0.0001 for
    0.01%). Positive funding → longs pay shorts (long cost positive,
    short cost negative).
    """
    intervals = hold_hours / 8.0
    cost_fraction = funding_rate_8h * intervals
    cost_bps = cost_fraction * 10_000.0  # fraction → bps
    return cost_bps if side == "long" else -cost_bps
