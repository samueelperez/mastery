"""Integration tests for HyperliquidClient against the real public endpoint.

Run with: pytest -m integration tests/liquidation/providers/test_hyperliquid_integration.py
Excluded from default run if the project ever filters `not integration` (it
does NOT today — there's no marker config in pyproject — but tagging is still
useful for future-proofing).
"""

from __future__ import annotations

import pytest

from app.liquidation.providers._hyperliquid_client import HyperliquidClient

pytestmark = pytest.mark.integration


async def test_all_mids_returns_btc_eth_sol() -> None:
    client = HyperliquidClient()
    try:
        mids = await client.all_mids()
        assert isinstance(mids, dict)
        assert "BTC" in mids
        assert "ETH" in mids
        assert "SOL" in mids
        assert float(mids["BTC"]) > 1000
    finally:
        await client.close()


async def test_meta_returns_perp_universe() -> None:
    client = HyperliquidClient()
    try:
        meta = await client.meta()
        assert "universe" in meta
        coins = [c["name"] for c in meta["universe"]]
        assert "BTC" in coins
    finally:
        await client.close()


async def test_clearinghouse_state_shape() -> None:
    """Use the zero address; validates response shape (will have no positions)."""
    client = HyperliquidClient()
    try:
        state = await client.clearinghouse_state("0x0000000000000000000000000000000000000000")
        # The zero address has no positions, but the response must have the
        # expected top-level shape.
        assert "assetPositions" in state
        assert isinstance(state["assetPositions"], list)
    finally:
        await client.close()
