"""Symbol mapping between internal convention and Hyperliquid's convention.

Internal: 'BTCUSDT', 'ETHUSDT', 'SOLUSDT' (matches Binance USDM).
Hyperliquid: 'BTC', 'ETH', 'SOL' (just the base asset; quote is implied USDC).
"""

from __future__ import annotations

# Forward map: internal -> Hyperliquid.
INTERNAL_TO_HYPERLIQUID: dict[str, str] = {
    "BTCUSDT": "BTC",
    "ETHUSDT": "ETH",
    "SOLUSDT": "SOL",
}

# Reverse map, built automatically.
HYPERLIQUID_TO_INTERNAL: dict[str, str] = {v: k for k, v in INTERNAL_TO_HYPERLIQUID.items()}


def to_hyperliquid(symbol: str) -> str:
    """Convert internal symbol to Hyperliquid coin name. Raises KeyError if
    unsupported."""
    try:
        return INTERNAL_TO_HYPERLIQUID[symbol]
    except KeyError:
        raise KeyError(
            f"Symbol {symbol!r} not mapped to Hyperliquid. "
            f"Supported: {sorted(INTERNAL_TO_HYPERLIQUID)}"
        ) from None


def to_internal(coin: str) -> str:
    """Convert Hyperliquid coin name to internal symbol. Raises KeyError."""
    try:
        return HYPERLIQUID_TO_INTERNAL[coin]
    except KeyError:
        raise KeyError(
            f"Hyperliquid coin {coin!r} not mapped. Known: {sorted(HYPERLIQUID_TO_INTERNAL)}"
        ) from None
