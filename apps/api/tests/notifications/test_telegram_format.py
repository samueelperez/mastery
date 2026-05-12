"""Pure-function tests for telegram formatting + bind flow constants.

Network-touching code (`_post`, webhook handler) is exercised in smoke
tests with a real bot token. Here we cover the pure formatting + structure
of payloads so a regression in the MarkdownV2 escape rules or the inline
keyboard shape is caught early.
"""

from __future__ import annotations

from app.agent.models import (
    Confluence,
    MarketRegime,
    Scenario,
    TradeIdea,
    TradeIdeaTarget,
)
from app.notifications.telegram import (
    _escape_md,
    _fmt_price,
    _inline_kb_for_setup,
    format_setup_alert,
)


def _idea(**overrides: object) -> TradeIdea:
    """Builds a minimal valid TradeIdea for testing."""
    base: dict[str, object] = {
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "regime": MarketRegime(label="trending_up", citations=[]),
        "confluences": [
            Confluence(
                timeframe="1h",
                bias="bull",
                narrative="Estructura HH/HL desde el bottom — bias bull intacto.",
                citations=[],
            )
        ],
        "scenarios": [
            Scenario(
                label="A",
                probability_pct=60,
                description="Continuación al alza si rompe el high reciente.",
                entry=80250.0,
                stop_loss=79500.0,
                target=82000.0,
            )
        ],
        "direction": "long",
        "entry": 80250.0,
        "stop_loss": 79500.0,
        "targets": [
            TradeIdeaTarget(
                label="TP1", price=82000.0, rationale="prev high", citations=[]
            )
        ],
        "confidence": "medium",
        "summary_es": (
            "Long en BTC 1h con confluencia HH/HL y entry en pullback al EMA21. "
            "RR objetivo 2.3:1. SL al swing low más reciente para minimizar drawdown."
        ),
        "leverage_x": 5,
        "position_size_pct": 1.0,
        "risk_notes": "Sizing 1% por R; SL ajustado al swing low. Validar funding pre-entrada.",
        "invalidation_conditions": [],
        "expires_at": None,
        "expires_at_rationale": None,
        "expires_at_citations": [],
    }
    base.update(overrides)
    return TradeIdea.model_validate(base)


def test_fmt_price_strips_trailing_zeros() -> None:
    assert _fmt_price(80250.0) == "80,250"
    assert _fmt_price(1.5) == "1.5"


def test_escape_md_handles_reserved_chars() -> None:
    """MarkdownV2 reserves _*[]()~`>#+-=|{}.! and backslash."""
    raw = "hello.world!"
    escaped = _escape_md(raw)
    assert "\\." in escaped
    assert "\\!" in escaped


def test_format_setup_alert_includes_key_fields() -> None:
    idea = _idea()
    msg = format_setup_alert(setup_id="setup-1", idea=idea)
    # Direction emoji + symbol + TF
    assert "LONG" in msg
    assert "BTCUSDT" in msg
    assert "1h" in msg
    # Entry / SL / TPs formatted with comma thousand separator (escaped by MarkdownV2)
    assert "80,250" in msg
    assert "79,500" in msg
    assert "82,000" in msg
    # Confidence + regime (regime label has an underscore, which MarkdownV2
    # escapes to `\_` — accept either form).
    assert "medium" in msg
    assert "trending_up" in msg or "trending\\_up" in msg


def test_format_setup_alert_short_for_short_direction() -> None:
    idea = _idea(
        direction="short",
        entry=80000.0,
        stop_loss=80500.0,
        targets=[
            TradeIdeaTarget(
                label="TP1", price=79000.0, rationale="prev low", citations=[]
            )
        ],
    )
    msg = format_setup_alert(setup_id="setup-2", idea=idea)
    assert "SHORT" in msg


def test_inline_kb_for_setup_has_three_buttons() -> None:
    """Approve + Reject in row 1, Open chart in row 2."""
    kb = _inline_kb_for_setup("abc-123")
    rows = kb["inline_keyboard"]
    assert len(rows) == 2
    assert len(rows[0]) == 2  # Approve, Reject
    assert rows[0][0]["callback_data"] == "a:abc-123"
    assert rows[0][1]["callback_data"] == "r:abc-123"
    assert len(rows[1]) == 1  # Open chart
    assert "url" in rows[1][0]
    assert "abc-123" in rows[1][0]["url"]


def test_inline_kb_callback_data_under_64_bytes() -> None:
    """Telegram limits callback_data to 64 bytes. Realistic UUIDs are ~36
    chars; with the `a:` prefix that's 38 — well under the limit."""
    long_id = "a" * 60  # contrived upper bound
    kb = _inline_kb_for_setup(long_id)
    for row in kb["inline_keyboard"]:
        for btn in row:
            cb = btn.get("callback_data")
            if cb is not None:
                assert len(cb.encode("utf-8")) <= 64, (
                    f"callback_data too long: {cb}"
                )


def test_summary_truncated_at_280_chars() -> None:
    """Telegram body sliced to keep message readable on mobile."""
    long_summary = "a" * 1000
    idea = _idea(summary_es=long_summary)
    msg = format_setup_alert(setup_id="x", idea=idea)
    # Count 'a' chars in the message — should be roughly 280, never 1000.
    a_count = msg.count("a")
    assert a_count <= 290  # 280 + small slack for other 'a's elsewhere
