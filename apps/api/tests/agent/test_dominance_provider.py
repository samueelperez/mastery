"""A.4 — Pure tests for dominance provider helpers.

`fetch_global_snapshot_live` and the Redis history helpers require network
+ Valkey and are exercised E2E; this file pins the pure logic that any
business decision keys off:

- `parse_coingecko_global`: payload → DominanceSnapshot.
- `classify_trend`: current vs prior dominance → direction + delta.
- `classify_regime`: BTC.D level + 7d trend → regime label.
"""

from __future__ import annotations

import pytest

from app.market.dominance.provider import (
    classify_regime,
    classify_trend,
    parse_coingecko_global,
)

# ----------------------------------------------------------------------------
# parse_coingecko_global
# ----------------------------------------------------------------------------


def _coingecko_response(*, btc: float = 52.0, eth: float = 17.0, total_usd: float = 2.3e12) -> dict:
    return {
        "data": {
            "market_cap_percentage": {"btc": btc, "eth": eth, "usdt": 5.0},
            "total_market_cap": {"usd": total_usd, "eur": total_usd * 0.92},
            "active_cryptocurrencies": 12000,
        }
    }


def test_parse_extracts_btc_eth_and_derives_other() -> None:
    snap = parse_coingecko_global(_coingecko_response(btc=52.5, eth=17.5))
    assert snap.btc_dominance_pct == pytest.approx(52.5)
    assert snap.eth_dominance_pct == pytest.approx(17.5)
    # other = 100 - 52.5 - 17.5 = 30.0
    assert snap.total3_share_pct == pytest.approx(30.0)
    assert snap.total_market_cap_usd == pytest.approx(2.3e12)


def test_parse_other_clamps_at_zero_for_oversum() -> None:
    snap = parse_coingecko_global(_coingecko_response(btc=70.0, eth=40.0))
    # 100 - 70 - 40 = -10 → clamp to 0
    assert snap.total3_share_pct == 0.0


def test_parse_raises_on_missing_data_key() -> None:
    with pytest.raises(ValueError, match="missing 'data'"):
        parse_coingecko_global({"meta": "unrelated"})


def test_parse_raises_on_missing_market_cap_percentage() -> None:
    with pytest.raises(ValueError, match="missing market_cap_percentage"):
        parse_coingecko_global({"data": {"total_market_cap": {"usd": 1.0}}})


def test_parse_raises_when_btc_or_eth_non_numeric() -> None:
    bad = {
        "data": {
            "market_cap_percentage": {"btc": "abc", "eth": 17.0},
            "total_market_cap": {"usd": 1.0},
        }
    }
    with pytest.raises(ValueError, match="non-numeric"):
        parse_coingecko_global(bad)


def test_parse_tolerates_missing_total_usd() -> None:
    bad_total = {
        "data": {
            "market_cap_percentage": {"btc": 52.0, "eth": 17.0},
            "total_market_cap": {"eur": 1.0},
        }
    }
    snap = parse_coingecko_global(bad_total)
    assert snap.total_market_cap_usd == 0.0


# ----------------------------------------------------------------------------
# classify_trend
# ----------------------------------------------------------------------------


def test_trend_indeterminate_without_prior() -> None:
    out = classify_trend(52.0, None)
    assert out.direction == "indeterminate"
    assert out.delta_pct == 0.0


def test_trend_flat_within_threshold() -> None:
    out = classify_trend(52.3, 52.0)  # delta 0.3pp < 0.5 default
    assert out.direction == "flat"
    assert out.delta_pct == pytest.approx(0.3)


def test_trend_up_over_threshold() -> None:
    out = classify_trend(53.0, 52.0)
    assert out.direction == "up"
    assert out.delta_pct == pytest.approx(1.0)


def test_trend_down_over_threshold() -> None:
    out = classify_trend(51.0, 52.0)
    assert out.direction == "down"
    assert out.delta_pct == pytest.approx(-1.0)


def test_trend_respects_custom_threshold() -> None:
    out = classify_trend(52.6, 52.0, flat_threshold=1.0)
    # delta 0.6pp < custom 1.0 → flat
    assert out.direction == "flat"


# ----------------------------------------------------------------------------
# classify_regime
# ----------------------------------------------------------------------------


def test_regime_range_when_7d_flat() -> None:
    r = classify_regime(
        btc_dominance_pct=55.0,
        btc_trend_1d="up",
        btc_trend_7d="flat",
    )
    assert r == "range"


def test_regime_range_when_7d_indeterminate() -> None:
    r = classify_regime(
        btc_dominance_pct=55.0,
        btc_trend_1d="up",
        btc_trend_7d="indeterminate",
    )
    assert r == "range"


def test_regime_btc_season_high_dom_up_trend() -> None:
    r = classify_regime(
        btc_dominance_pct=54.0,
        btc_trend_1d="up",
        btc_trend_7d="up",
    )
    assert r == "btc_season"


def test_regime_alt_season_low_dom_down_trend() -> None:
    r = classify_regime(
        btc_dominance_pct=45.0,
        btc_trend_1d="down",
        btc_trend_7d="down",
    )
    assert r == "alt_season"


def test_regime_mixed_when_level_in_middle_band() -> None:
    # BTC.D 50% (between 47 and 53) with up trend → not btc_season, not alt → mixed
    r = classify_regime(
        btc_dominance_pct=50.0,
        btc_trend_1d="up",
        btc_trend_7d="up",
    )
    assert r == "mixed"


def test_regime_mixed_on_conflicting_signals() -> None:
    # High dominance but trending DOWN → not btc_season (needs up/flat),
    # not alt_season (needs dom<47). Falls to mixed.
    r = classify_regime(
        btc_dominance_pct=55.0,
        btc_trend_1d="down",
        btc_trend_7d="down",
    )
    assert r == "mixed"
