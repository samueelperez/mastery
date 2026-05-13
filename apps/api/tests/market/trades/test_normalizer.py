"""Tests for `normalize_ccxt_trade`. Pure-function; no DB."""

from datetime import UTC, datetime

import pytest

from app.core.exchanges.normalizer import normalize_ccxt_trade


def _ccxt_row(**overrides) -> dict:
    base = {
        "timestamp": 1_715_500_000_000,  # 2024-05-12T08:26:40Z
        "symbol": "BTCUSDT",
        "side": "buy",
        "price": 84_500.5,
        "amount": 1.5,
        "id": "12345",
    }
    base.update(overrides)
    return base


class TestNormalizeCcxtTrade:
    def test_buy_maps_to_B(self) -> None:
        t = normalize_ccxt_trade(_ccxt_row(side="buy"), exchange="binance_usdm")
        assert t.side == "B"

    def test_sell_maps_to_S(self) -> None:
        t = normalize_ccxt_trade(_ccxt_row(side="sell"), exchange="binance_usdm")
        assert t.side == "S"

    def test_invalid_side_raises(self) -> None:
        with pytest.raises(ValueError, match="Unexpected trade side"):
            normalize_ccxt_trade(_ccxt_row(side="weird"), exchange="binance_usdm")

    def test_timestamp_becomes_tz_aware(self) -> None:
        t = normalize_ccxt_trade(_ccxt_row(), exchange="binance_usdm")
        assert t.ts.tzinfo == UTC
        # 1_715_500_000_000 ms → 2024-05-12T07:46:40Z
        assert t.ts == datetime(2024, 5, 12, 7, 46, 40, tzinfo=UTC)

    def test_missing_trade_id_is_none(self) -> None:
        row = _ccxt_row()
        del row["id"]
        t = normalize_ccxt_trade(row, exchange="binance_usdm")
        assert t.trade_id is None

    def test_explicit_none_trade_id(self) -> None:
        t = normalize_ccxt_trade(_ccxt_row(id=None), exchange="binance_usdm")
        assert t.trade_id is None

    def test_trade_id_stringified(self) -> None:
        # Some exchanges return numeric ids.
        t = normalize_ccxt_trade(_ccxt_row(id=987654321), exchange="binance_usdm")
        assert t.trade_id == "987654321"

    def test_exchange_propagated(self) -> None:
        t = normalize_ccxt_trade(_ccxt_row(), exchange="binance_usdm")
        assert t.exchange == "binance_usdm"

    def test_symbol_from_row(self) -> None:
        t = normalize_ccxt_trade(_ccxt_row(symbol="ETHUSDT"), exchange="binance_usdm")
        assert t.symbol == "ETHUSDT"

    def test_price_and_size_floats(self) -> None:
        t = normalize_ccxt_trade(
            _ccxt_row(price="84500.5", amount="1.25"),  # ccxt may return strings
            exchange="binance_usdm",
        )
        assert t.price == 84_500.5
        assert t.size == 1.25
