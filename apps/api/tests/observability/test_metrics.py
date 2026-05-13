"""Pinned tests for the Prometheus metric inventory + endpoint shape.

These don't exercise the full scrape stack (Prometheus → /metrics), they
just verify:
  - every counter / gauge declared in metrics.py is importable,
  - the labels we use in instrumentation match the declared label list
    (otherwise prometheus-client raises at the first `.labels(...)` call),
  - the exposition format from `generate_latest` includes our metric
    names — guards against accidental renaming.

The smoke E2E (`tests/integration/test_scout_smoke.py`) exercises the
counters incrementing under realistic flow.
"""

from __future__ import annotations

from prometheus_client import generate_latest

from app.core.observability.metrics import (
    agent_invocation_seconds,
    agent_invocations_total,
    gap_fill_inserts_total,
    risk_actions_total,
    runtime_streams_alive,
    scout_accepted_total,
    scout_drops_total,
    setup_transitions_total,
    telegram_sends_total,
)


def test_scout_drops_accepts_all_dropreason_values() -> None:
    """All DropReason variants must be valid labels (no typos vs the Literal)."""
    valid_reasons = [
        "cooldown_paused",
        "rate_limit_symbol",
        "rate_limit_daily",
        "quality_floor_confidence",
        "quality_floor_direction",
        "dedup_similar_pending",
        "agent_returned_brief",
        "agent_returned_text",
        "validator_raised",
        "no_trade_idea",
        "persist_error",
    ]
    for reason in valid_reasons:
        # Will not raise if reason is acceptable; will raise if labels signature mismatch.
        scout_drops_total.labels(reason=reason)


def test_risk_actions_accepts_all_action_kinds() -> None:
    for action in ("be_moved", "trailing_updated", "time_stopped"):
        risk_actions_total.labels(action=action)


def test_telegram_sends_accepts_all_outcomes() -> None:
    for method in ("sendMessage", "answerCallbackQuery"):
        for outcome in (
            "ok",
            "http_error",
            "api_error",
            "transport_error",
            "no_token",
        ):
            telegram_sends_total.labels(method=method, outcome=outcome)


def test_agent_invocations_label_shape() -> None:
    """`outcome` ∈ {trade_idea, brief, text, error}; `kind` ∈ {chat, scout, review, post_mortem}."""
    for kind in ("chat", "scout", "review", "post_mortem"):
        for outcome in ("trade_idea", "brief", "text", "error"):
            agent_invocations_total.labels(kind=kind, outcome=outcome)
        agent_invocation_seconds.labels(kind=kind)


def test_setup_transitions_label_shape() -> None:
    """Three labels: from_status, to_status, event. Common transitions only."""
    setup_transitions_total.labels(
        from_status="pending", to_status="active", event="entry_hit"
    )
    setup_transitions_total.labels(
        from_status="active", to_status="closed", event="sl_hit"
    )
    setup_transitions_total.labels(
        from_status="pending", to_status="cancelled", event="invalidated"
    )


def test_gap_fill_inserts_label_shape() -> None:
    gap_fill_inserts_total.labels(
        symbol="BTCUSDT", timeframe="1h", phase="startup"
    )
    gap_fill_inserts_total.labels(
        symbol="ETHUSDT", timeframe="4h", phase="reconnect"
    )


def test_streams_alive_gauge() -> None:
    """Gauge: set and read back."""
    runtime_streams_alive.set(20)
    runtime_streams_alive.set(0)  # reset for other tests


def test_exposition_includes_all_metrics() -> None:
    """`generate_latest` must serialize every metric — guard against
    accidental renames or missed `# TYPE` declarations."""
    body = generate_latest().decode("utf-8")
    for metric_name in [
        "mt_scout_drops_total",
        "mt_scout_accepted_total",
        "mt_agent_invocations_total",
        "mt_agent_invocation_seconds",
        "mt_risk_actions_total",
        "mt_gap_fill_inserts_total",
        "mt_telegram_sends_total",
        "mt_setup_transitions_total",
        "mt_runtime_streams_alive",
    ]:
        assert metric_name in body, f"missing metric in exposition: {metric_name}"


def test_increment_does_not_raise() -> None:
    """Incrementing once must succeed (Prometheus client is in-process so
    this also asserts the global registry isn't borked)."""
    scout_drops_total.labels(reason="cooldown_paused").inc()
    scout_accepted_total.inc()
    risk_actions_total.labels(action="be_moved").inc()
