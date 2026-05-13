"""Unit tests for HyperliquidLiquidationProvider with mocked HTTP."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exchanges.hyperliquid_symbols import (
    to_hyperliquid,
    to_internal,
)
from app.liquidation.providers.hyperliquid import HyperliquidLiquidationProvider


def _fake_clearinghouse_response(
    coin: str,
    liq_px: float,
    side_long: bool,
    notional: float = 422_500,
) -> dict:
    """Build a synthetic clearinghouseState payload with one position."""
    szi = "5.0" if side_long else "-5.0"
    return {
        "assetPositions": [
            {
                "position": {
                    "coin": coin,
                    "szi": szi,
                    "liquidationPx": str(liq_px),
                    "positionValue": str(notional),
                }
            }
        ],
        "marginSummary": {},
        "time": int(datetime.now(tz=UTC).timestamp() * 1000),
    }


@pytest.fixture
def mock_hl_client():
    client = AsyncMock()
    client.all_mids = AsyncMock(return_value={"BTC": "84500", "ETH": "3200"})
    return client


@pytest.fixture
def mock_session_factory_with_addresses():
    """Session whose SELECT returns 3 fake addresses."""

    def _make_session() -> AsyncMock:
        session = AsyncMock()
        fake_rows = [
            MagicMock(address=f"0x{'a' * 40}"),
            MagicMock(address=f"0x{'b' * 40}"),
            MagicMock(address=f"0x{'c' * 40}"),
        ]
        result = MagicMock()
        result.__iter__ = lambda self: iter(fake_rows)
        session.execute = AsyncMock(return_value=result)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        return session

    return MagicMock(side_effect=lambda: _make_session())


@pytest.fixture
def mock_session_factory_empty():
    """Session whose SELECT returns no rows."""

    def _make_session() -> AsyncMock:
        session = AsyncMock()
        result = MagicMock()
        result.__iter__ = lambda self: iter([])
        session.execute = AsyncMock(return_value=result)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        return session

    return MagicMock(side_effect=lambda: _make_session())


class TestSymbolMapping:
    def test_to_hyperliquid_known(self) -> None:
        assert to_hyperliquid("BTCUSDT") == "BTC"
        assert to_hyperliquid("ETHUSDT") == "ETH"
        assert to_hyperliquid("SOLUSDT") == "SOL"

    def test_to_hyperliquid_unknown_raises(self) -> None:
        with pytest.raises(KeyError, match="not mapped"):
            to_hyperliquid("DOGEUSDT")

    def test_to_internal_reverse(self) -> None:
        assert to_internal("BTC") == "BTCUSDT"
        assert to_internal("ETH") == "ETHUSDT"
        assert to_internal("SOL") == "SOLUSDT"


class TestHyperliquidProvider:
    async def test_unsupported_symbol(
        self, mock_session_factory_with_addresses, mock_hl_client
    ) -> None:
        p = HyperliquidLiquidationProvider(mock_session_factory_with_addresses, mock_hl_client)
        result = await p.get_heatmap("DOGEUSDT", "4h", 0.10)
        assert result.buckets == []
        assert any("symbol_not_supported" in w for w in result.warnings)

    async def test_empty_address_universe(self, mock_session_factory_empty, mock_hl_client) -> None:
        p = HyperliquidLiquidationProvider(mock_session_factory_empty, mock_hl_client)
        result = await p.get_heatmap("BTCUSDT", "4h", 84_500.0)
        assert result.buckets == []
        assert "address_universe_empty" in result.warnings

    async def test_aggregates_long_and_short_positions(
        self, mock_session_factory_with_addresses, mock_hl_client
    ) -> None:
        # 3 addresses → 3 different positions.
        mock_hl_client.clearinghouse_state = AsyncMock(
            side_effect=[
                _fake_clearinghouse_response("BTC", 82_000, side_long=True),
                _fake_clearinghouse_response("BTC", 86_500, side_long=False),
                _fake_clearinghouse_response("BTC", 82_100, side_long=True),
            ]
        )
        p = HyperliquidLiquidationProvider(mock_session_factory_with_addresses, mock_hl_client)
        result = await p.get_heatmap("BTCUSDT", "4h", 84_500.0, max_distance_pct=5.0)

        long_liq = [b for b in result.buckets if b.side == "long_liq"]
        short_liq = [b for b in result.buckets if b.side == "short_liq"]
        assert len(long_liq) >= 1
        assert len(short_liq) >= 1
        # All within 5% of 84_500 = [80_275, 88_725]
        for b in result.buckets:
            assert 80_275 <= b.price_low <= 88_725

    async def test_continues_on_address_errors(
        self, mock_session_factory_with_addresses, mock_hl_client
    ) -> None:
        mock_hl_client.clearinghouse_state = AsyncMock(
            side_effect=[
                _fake_clearinghouse_response("BTC", 82_000, side_long=True),
                Exception("network"),  # one address fails
                _fake_clearinghouse_response("BTC", 86_500, side_long=False),
            ]
        )
        p = HyperliquidLiquidationProvider(mock_session_factory_with_addresses, mock_hl_client)
        result = await p.get_heatmap("BTCUSDT", "4h", 84_500.0)
        assert any("clearinghouse_errors:1" in w for w in result.warnings)
        assert len(result.buckets) >= 1

    async def test_skips_other_coins(
        self, mock_session_factory_with_addresses, mock_hl_client
    ) -> None:
        # Address has positions in ETH, not BTC.
        mock_hl_client.clearinghouse_state = AsyncMock(
            side_effect=[
                _fake_clearinghouse_response("ETH", 3200, side_long=True),
                _fake_clearinghouse_response("ETH", 3300, side_long=False),
                _fake_clearinghouse_response("ETH", 3250, side_long=True),
            ]
        )
        p = HyperliquidLiquidationProvider(mock_session_factory_with_addresses, mock_hl_client)
        result = await p.get_heatmap("BTCUSDT", "4h", 84_500.0)
        assert result.buckets == []

    async def test_handles_none_liquidation_px(
        self, mock_session_factory_with_addresses, mock_hl_client
    ) -> None:
        """Positions with `liquidationPx: None` (e.g. pre-margin) are skipped."""
        weird = {
            "assetPositions": [
                {
                    "position": {
                        "coin": "BTC",
                        "szi": "5.0",
                        "liquidationPx": None,  # uncommitted margin
                        "positionValue": "422500",
                    }
                }
            ],
            "marginSummary": {},
            "time": int(datetime.now(tz=UTC).timestamp() * 1000),
        }
        mock_hl_client.clearinghouse_state = AsyncMock(side_effect=[weird, weird, weird])
        p = HyperliquidLiquidationProvider(mock_session_factory_with_addresses, mock_hl_client)
        result = await p.get_heatmap("BTCUSDT", "4h", 84_500.0)
        assert result.buckets == []
