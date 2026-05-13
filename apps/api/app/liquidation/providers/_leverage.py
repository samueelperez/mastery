"""Leverage and maintenance margin constants.

Shared by all providers and tests. Values are based on Hyperliquid's
maintenance margin formula (half of initial margin at max leverage). Binance
and Bybit have similar tiered structures; for the derived provider we use
this approximation since we don't know which exchange a counterparty was on.
"""

from __future__ import annotations

# Leverage brackets used in liquidation estimation.
# Distribution weights are empirical priors: most retail uses 5-25x.
LEVERAGE_BRACKETS: tuple[int, ...] = (3, 5, 10, 25, 50, 75, 100)

LEVERAGE_PRIOR_WEIGHTS: dict[int, float] = {
    3: 0.10,
    5: 0.20,
    10: 0.30,
    25: 0.20,
    50: 0.10,
    75: 0.05,
    100: 0.05,
}

# Maintenance margin as fraction (e.g. 0.005 = 0.5%).
# Computed as ~half of initial margin (1/leverage).
MAINTENANCE_MARGIN: dict[int, float] = {
    3: 0.167,
    5: 0.10,
    10: 0.05,
    25: 0.02,
    50: 0.01,
    75: 0.0067,
    100: 0.005,
}

assert sum(LEVERAGE_PRIOR_WEIGHTS.values()) == 1.0, (
    f"Leverage prior weights must sum to 1.0, got {sum(LEVERAGE_PRIOR_WEIGHTS.values())}"
)


def estimate_liq_price(trade_px: float, side: str, leverage: int) -> float:
    """Estimate the liquidation price of the counterparty's position.

    Args:
        trade_px: Price at which the trade executed.
        side: Aggressor side. 'B' (buy) means the counterparty went short, so
            their liquidation is ABOVE the entry. 'S' (sell) means the
            counterparty went long, so their liquidation is BELOW.
        leverage: Assumed leverage of the counterparty's position.

    Returns:
        Estimated liquidation price.

    Notes:
        This is a heuristic: we don't actually know the counterparty's
        leverage. The service weights this estimate by LEVERAGE_PRIOR_WEIGHTS
        across all brackets.
    """
    if leverage not in MAINTENANCE_MARGIN:
        raise ValueError(f"Unsupported leverage: {leverage}")
    mm = MAINTENANCE_MARGIN[leverage]
    if side == "B":  # counterparty is SHORT, liquidation is ABOVE
        return trade_px * (1 + 1 / leverage - mm)
    elif side == "S":  # counterparty is LONG, liquidation is BELOW
        return trade_px * (1 - 1 / leverage + mm)
    else:
        raise ValueError(f"Invalid side: {side!r}. Expected 'B' or 'S'.")


def opposite_side(trade_side: str) -> str:
    """Map trade aggressor side to the side that gets liquidated.

    A buy aggressor lifts the offer (someone sold short, may liquidate up).
    A sell aggressor hits the bid (someone bought long, may liquidate down).
    """
    if trade_side == "B":
        return "short_liq"
    elif trade_side == "S":
        return "long_liq"
    else:
        raise ValueError(f"Invalid side: {trade_side!r}")
