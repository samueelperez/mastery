"""Smoke E2E: scout dispatch → setup persist → approval gate → activation.

This is the FIRST integration test that hits the real Postgres + the full
storage layer. The agent is mocked (it would otherwise need OpenRouter +
a real model call), and `_evaluate_setup` is invoked directly with a
synthetic candle rather than via the pub/sub watcher.

Coverage:

  1. `dispatch_scout_match` with a mocked agent returning a valid TradeIdea
     → setup persisted with `source='scout_proposal'`, `status='pending'`.
  2. Without an `approved` event, `_evaluate_setup` on an entry-hit candle
     does NOT transition the setup to active (the gate works against a real
     DB, not just stubs).
  3. `approve_setup` endpoint writes the audit event correctly.
  4. After approval, `_evaluate_setup` on the same candle DOES transition
     pending → active.
  5. Prometheus counters (`scout_accepted_total`, `setup_transitions_total`)
     incremented as expected.

The test uses a deterministic user_id + cleans the DB rows it touched so
re-runs don't accumulate. If the suite is run in parallel against the same
DB, the user_id keeps things isolated.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import app.setups.scout_dispatcher as scout_dispatcher
import app.setups.runtime as setup_runtime
import pytest
from sqlalchemy import text

from app.agent.models import (
    Confluence,
    MarketRegime,
    Scenario,
    TradeIdea,
    TradeIdeaTarget,
)
from app.core.db import session_scope
from app.core.observability.metrics import (
    scout_accepted_total,
    setup_transitions_total,
)
from app.setups.repo import has_approval_event
from app.setups.scout_dispatcher import dispatch_scout_match

# Stable user_id so re-runs of the suite touch the same rows and the
# cleanup at module teardown wipes them.
TEST_USER_ID = "smoke-test-scout-user"


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _idea() -> TradeIdea:
    """Builds a valid TradeIdea the scout dispatcher will persist."""
    return TradeIdea.model_validate(
        {
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "regime": MarketRegime(label="trending_up", citations=[]),
            "confluences": [
                Confluence(
                    timeframe="1h",
                    bias="bull",
                    narrative="EMA21>55 con pendiente positiva — estructura HH/HL.",
                    citations=[],
                )
            ],
            "scenarios": [
                Scenario(
                    label="A",
                    probability_pct=60,
                    description="Continuación al alza si rompe el high reciente.",
                    entry=100.0,
                    stop_loss=95.0,
                    target=110.0,
                )
            ],
            "direction": "long",
            "entry": 100.0,
            "stop_loss": 95.0,
            "targets": [
                TradeIdeaTarget(
                    label="TP1", price=110.0, rationale="prev high", citations=[]
                )
            ],
            "confidence": "medium",
            "summary_es": "Long en BTC 1h con confluencia HH/HL y entry en pullback al EMA21.",
            "leverage_x": 3,
            "position_size_pct": 1.0,
            "risk_notes": "Sizing 1% por R; validar funding pre-entry.",
            "invalidation_conditions": [],
            "expires_at": None,
            "expires_at_rationale": None,
            "expires_at_citations": [],
        }
    )


class _MockResult:
    """Mimics pydantic-ai's `RunResult.output`."""

    def __init__(self, output: Any) -> None:
        self.output = output


class _MockAgent:
    async def run(self, _user_message: str, deps: Any = None) -> _MockResult:
        return _MockResult(_idea())


async def _cleanup_test_rows() -> bool:
    """Wipe rows from previous runs so the test is deterministic.
    Retries once because asyncpg connection cleanup is async and an unawaited
    `Connection._cancel` from the previous test's session can transiently
    fail the next session open."""
    import asyncio as _asyncio

    for attempt in range(2):
        try:
            async with session_scope() as session:
                await session.execute(
                    text(
                        "DELETE FROM setup_events WHERE trade_id IN ("
                        "SELECT id FROM journal_trades WHERE user_id = :uid)"
                    ),
                    {"uid": TEST_USER_ID},
                )
                await session.execute(
                    text("DELETE FROM journal_trades WHERE user_id = :uid"),
                    {"uid": TEST_USER_ID},
                )
            return True
        except Exception:
            if attempt == 0:
                # Give asyncpg a tick to finish its pending Connection._cancel.
                await _asyncio.sleep(0.1)
                continue
            return False
    return False


async def _require_db() -> None:
    """Skip the test cleanly if Postgres isn't reachable. Use this instead
    of letting cleanup raise — that way a transient hiccup between tests
    doesn't poison the cleanup as a "DB down" verdict."""
    if not await _cleanup_test_rows():
        pytest.skip("DB not reachable for smoke E2E")


async def _fetch_setup(setup_id: str) -> dict[str, Any] | None:
    async with session_scope() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT id::text AS id, status, source, entry_px, stop_loss_px
                    FROM journal_trades
                    WHERE id = CAST(:tid AS uuid)
                    """
                ),
                {"tid": setup_id},
            )
        ).mappings().one_or_none()
        return dict(row) if row else None


# -----------------------------------------------------------------------------
# Smoke E2E
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scout_to_setup_to_approval_smoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full path: dispatch_scout_match → setup persisted with scout_proposal
    source → approval gate blocks transition without 'approved' event →
    approve_setup writes event → has_approval_event returns True.

    Skips if Postgres isn't reachable (DB-less CI shouldn't fail this test)."""
    await _require_db()

    # Mock the agent so we don't hit OpenRouter.
    monkeypatch.setattr(scout_dispatcher, "get_agent", lambda: _MockAgent())

    # Snapshot of starting counter values so we can assert deltas.
    accepted_before = scout_accepted_total._value.get()  # type: ignore[attr-defined]

    # --- Step 1: dispatch_scout_match persists the setup ---------------------
    verdict = await dispatch_scout_match(
        user_id=TEST_USER_ID,
        rule_id="00000000-0000-0000-0000-000000000099",
        rule_name="smoke-test-rule",
        symbol="BTCUSDT",
        timeframe="1h",
        snapshot={"rsi": 32.0, "close": 100.0},
        fired_at=datetime.now(tz=UTC),
    )
    assert verdict.accepted is True, f"verdict was: {verdict}"
    assert verdict.setup_id is not None
    setup_id = verdict.setup_id

    # Counter incremented.
    accepted_after = scout_accepted_total._value.get()  # type: ignore[attr-defined]
    assert accepted_after == accepted_before + 1

    # --- Step 2: setup row has scout_proposal source -------------------------
    row = await _fetch_setup(setup_id)
    assert row is not None
    assert row["source"] == "scout_proposal"
    assert row["status"] == "pending"
    assert float(row["entry_px"]) == 100.0
    assert float(row["stop_loss_px"]) == 95.0

    # --- Step 3: approval gate blocks transition without `approved` event ---
    # Verify directly that has_approval_event returns False.
    async with session_scope() as session:
        approved = await has_approval_event(session, trade_id=setup_id)
    assert approved is False, "no `approved` event should exist yet"

    # --- Step 4: approve_setup writes the event ------------------------------
    from app.setups.routes import approve_setup

    approve_result = await approve_setup(setup_id, TEST_USER_ID)
    assert approve_result["status"] == "approved"

    # --- Step 5: has_approval_event now returns True ------------------------
    async with session_scope() as session:
        approved = await has_approval_event(session, trade_id=setup_id)
    assert approved is True

    # --- Step 6: re-approval is idempotent ----------------------------------
    re_approve = await approve_setup(setup_id, TEST_USER_ID)
    assert re_approve["status"] == "already_approved"

    # Cleanup.
    await _cleanup_test_rows()


@pytest.mark.asyncio
async def test_scout_drop_increments_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that a scout drop (no_trade direction → quality_floor_direction)
    increments the right counter — guards the wiring between drop reason
    and Prometheus label."""
    try:
        await _cleanup_test_rows()
    except Exception as exc:
        pytest.skip(f"DB not reachable, skipping smoke: {type(exc).__name__}")

    # Agent returns a 'no_trade' TradeIdea (quality floor rejects).
    no_trade_idea = TradeIdea.model_validate(
        {
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "regime": MarketRegime(label="ranging", citations=[]),
            "confluences": [],
            "scenarios": [],
            "direction": "no_trade",
            "entry": None,
            "stop_loss": None,
            "targets": [],
            "confidence": "low",
            "summary_es": "Range sin estructura clara; no operar.",
            "leverage_x": None,
            "position_size_pct": None,
            "risk_notes": "Esperar definición de bias.",
            "invalidation_conditions": [],
            "expires_at": None,
            "expires_at_rationale": None,
            "expires_at_citations": [],
        }
    )

    class _NoTradeAgent:
        async def run(self, _msg: str, deps: Any = None) -> _MockResult:
            return _MockResult(no_trade_idea)

    monkeypatch.setattr(scout_dispatcher, "get_agent", lambda: _NoTradeAgent())

    drops_before = scout_dispatcher.scout_drops_total.labels(
        reason="quality_floor_direction"
    )._value.get()  # type: ignore[attr-defined]

    verdict = await dispatch_scout_match(
        user_id=TEST_USER_ID,
        rule_id="00000000-0000-0000-0000-000000000098",
        rule_name="smoke-no-trade",
        symbol="BTCUSDT",
        timeframe="1h",
        snapshot={"rsi": 50.0},
        fired_at=datetime.now(tz=UTC),
    )
    assert verdict.accepted is False
    assert verdict.drop_reason == "quality_floor_direction"

    drops_after = scout_dispatcher.scout_drops_total.labels(
        reason="quality_floor_direction"
    )._value.get()  # type: ignore[attr-defined]
    assert drops_after == drops_before + 1


@pytest.mark.asyncio
async def test_evaluate_setup_respects_approval_gate_with_real_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inserts a scout_proposal setup directly (skip dispatcher), then
    calls `_evaluate_setup` with a candle that hits the entry. Verifies the
    gate against the REAL DB has_approval_event query — covers a path the
    monkey-patched unit tests can't (real SQL + real CHECK constraints)."""
    await _require_db()

    # Insert a scout_proposal setup pending. JSONB targets are passed as a
    # parameter rather than inlined so SQLAlchemy's `:` bind-param parser
    # doesn't choke on the `"price":110.0` substring.
    import json as _json

    proposed_at = datetime.now(tz=UTC) - timedelta(minutes=5)
    targets_json = _json.dumps(
        [{"label": "TP1", "price": 110.0, "rationale": "prev high"}]
    )
    async with session_scope() as session:
        result = await session.execute(
            text(
                """
                INSERT INTO journal_trades (
                    user_id, trade_ts, symbol, timeframe, mode, side, status,
                    source, entry_px, stop_loss_px, size, setup_tag, regime,
                    confidence, targets, summary_text, summary_hash,
                    proposed_at, mistakes
                ) VALUES (
                    :uid, :ts, 'BTCUSDT', '1h', 'manual_log', 'long', 'pending',
                    'scout_proposal', 100.0, 95.0, 1.0, 'long_trending_up_1h',
                    'trending_up', 'medium',
                    CAST(:targets AS jsonb),
                    'smoke test setup', 'smoke-hash', :ts, NULL
                )
                RETURNING id::text
                """
            ),
            {"uid": TEST_USER_ID, "ts": proposed_at, "targets": targets_json},
        )
        setup_id = result.scalar_one()
        # Need the audit `proposed` event so the timeline isn't empty (defensive).
        await session.execute(
            text(
                "INSERT INTO setup_events (trade_id, event, candle_ts, payload) "
                "VALUES (CAST(:tid AS uuid), 'proposed', :ts, '{}'::jsonb)"
            ),
            {"tid": setup_id, "ts": proposed_at},
        )

    # Load it as OpenSetupRow.
    from app.setups.repo import list_open_setups

    async with session_scope() as session:
        opens = await list_open_setups(session)
    setup = next((s for s in opens if s.id == setup_id), None)
    assert setup is not None, "inserted setup not found in list_open_setups"
    assert setup.source == "scout_proposal"

    # --- Try to activate WITHOUT approval — gate must block ----------------
    # Use a candle that hits the entry (low=99, high=101 straddles entry=100).
    candle_ts = datetime.now(tz=UTC)
    transitions_before = setup_transitions_total.labels(
        from_status="pending", to_status="active", event="entry_hit"
    )._value.get()  # type: ignore[attr-defined]

    # Disable risk_manager + reviews so we exercise the pure gate path.
    monkeypatch.setattr(
        setup_runtime, "_fire_review", lambda **_kw: None
    )

    await setup_runtime._evaluate_setup(
        setup, high=101.0, low=99.0, close=100.5, candle_ts=candle_ts
    )

    transitions_after = setup_transitions_total.labels(
        from_status="pending", to_status="active", event="entry_hit"
    )._value.get()  # type: ignore[attr-defined]
    assert transitions_after == transitions_before, (
        "scout setup activated without approval — gate broken"
    )

    # Re-read status — must still be pending.
    row = await _fetch_setup(setup_id)
    assert row is not None
    assert row["status"] == "pending"

    # --- Approve and retry — now transition fires ---------------------------
    from app.setups.routes import approve_setup

    await approve_setup(setup_id, TEST_USER_ID)

    # Re-load (entry_hit_at change isn't relevant here; we just need fresh row).
    async with session_scope() as session:
        opens = await list_open_setups(session)
    setup = next((s for s in opens if s.id == setup_id), None)
    assert setup is not None

    await setup_runtime._evaluate_setup(
        setup, high=101.0, low=99.0, close=100.5, candle_ts=candle_ts
    )

    row = await _fetch_setup(setup_id)
    assert row is not None
    assert row["status"] == "active", (
        f"approved scout setup did not transition; status={row['status']}"
    )

    # Cleanup.
    await _cleanup_test_rows()
