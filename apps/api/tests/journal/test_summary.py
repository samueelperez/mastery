"""Pure-Python tests of the summary text builder + hash."""

from __future__ import annotations

from app.journal.summary import build_summary_text, hash_summary


def test_summary_orders_discriminative_fields_first() -> None:
    text = build_summary_text(
        {
            "setup_tag": "breakout_4h",
            "regime": "trending_up",
            "side": "long",
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "r_multiple": 1.85,
            "mistakes": "entré tarde",
        }
    )
    # Discriminator lead matters: setup_tag and regime first, then symbol/tf.
    assert text.startswith("breakout_4h | trending_up | BTCUSDT 4h long")
    assert "win +1.85R" in text
    assert "mistakes: entré tarde" in text


def test_summary_handles_missing_fields() -> None:
    text = build_summary_text(
        {"setup_tag": "test", "regime": "trending_up", "side": "long", "symbol": "BTC", "timeframe": "1h"}
    )
    assert text.startswith("test | trending_up | BTC 1h long")
    assert "open" in text
    assert "mistakes:" not in text


def test_summary_classifies_outcome() -> None:
    base = {"setup_tag": "x", "regime": "y", "side": "long", "symbol": "BTC", "timeframe": "1h"}
    assert "win +1.20R" in build_summary_text({**base, "r_multiple": 1.2})
    assert "loss -0.80R" in build_summary_text({**base, "r_multiple": -0.8})
    assert "scratch" in build_summary_text({**base, "r_multiple": 0.0})


def test_hash_is_stable_and_unique() -> None:
    a = build_summary_text({"setup_tag": "a", "regime": "b", "side": "long", "symbol": "BTC", "timeframe": "1h"})
    b = build_summary_text({"setup_tag": "a", "regime": "c", "side": "long", "symbol": "BTC", "timeframe": "1h"})
    assert hash_summary(a) == hash_summary(a)
    assert hash_summary(a) != hash_summary(b)
