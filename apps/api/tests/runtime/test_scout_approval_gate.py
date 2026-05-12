"""Blocker 1 — SetupRuntime gates scout proposals on `approved` event.

The pending → active transition path in `_evaluate_setup` is a DB-touching
async coroutine, but the GATE itself is a synchronous predicate
(`setup.source == 'scout_proposal' AND not approved`). These tests cover
the predicate by stubbing `has_approval_event` and asserting that
`_evaluate_setup` does NOT call `transition_status` when the gate blocks.

The test pattern uses `monkeypatch` to swap the imported helpers inside
the runtime module — that's the lowest-friction way to isolate the gate
logic without spinning up a full Postgres + AlertsRuntime in pytest.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

import app.runtime.setup_runtime as setup_runtime
from app.storage.setup_repo import OpenSetupRow


def _setup(*, source: str, side: str = "long") -> OpenSetupRow:
    """Builds an `OpenSetupRow` that's `pending` with entry/SL set so the
    candle in the test below hits the entry trigger. `source` is the
    discriminator under test."""
    return OpenSetupRow(
        id="00000000-0000-0000-0000-000000000001",
        user_id="u",
        symbol="BTCUSDT",
        timeframe="1h",
        side=side,
        status="pending",
        entry_px=100.0,
        stop_loss_px=95.0,
        targets=[{"label": "TP1", "price": 110.0}],
        invalidation_conditions=[],
        expires_at=None,
        proposed_at=datetime(2026, 5, 12, 6, 0, tzinfo=UTC),
        entry_hit_at=None,
        source=source,
    )


CANDLE_TS = datetime(2026, 5, 12, 7, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _patch_session_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_evaluate_setup` opens DB sessions via `session_scope`; we don't want
    actual DB hits. The fixture monkey-patches `session_scope` so the
    `SELECT ... FOR UPDATE` (added by the audit race-fix) returns 'pending'
    by default — tests that need a different observed status override it
    via `setup_runtime._test_observed_status`.
    The patched helpers (`has_approval_event`, `transition_status`) stay
    monkeypatched per-test below."""

    class _StubResult:
        def __init__(self, value: Any) -> None:
            self._value = value

        def scalar_one_or_none(self) -> Any:
            return self._value

    class _DummySession:
        async def execute(self, *_args: Any, **_kwargs: Any) -> _StubResult:
            # The runtime now issues `SELECT status FROM journal_trades ...
            # FOR UPDATE` inside the pending branch. The default observed
            # status is 'pending' so the test path proceeds; override on the
            # module if a test needs the no-op branch.
            return _StubResult(
                getattr(setup_runtime, "_test_observed_status", "pending")
            )

    class _DummyCtx:
        async def __aenter__(self) -> _DummySession:
            return _DummySession()

        async def __aexit__(self, *_args: Any) -> None:
            return None

    monkeypatch.setattr(setup_runtime, "session_scope", lambda: _DummyCtx())


async def test_scout_proposal_without_approval_does_not_activate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Source=scout_proposal + no `approved` event → entry hit must NOT
    transition the setup to active. The runtime returns silently and the
    setup stays pending."""
    setup = _setup(source="scout_proposal")

    async def _no_approval(*_args: Any, **_kwargs: Any) -> bool:
        return False

    transition_calls: list[dict[str, Any]] = []

    async def _record_transition(*_args: Any, **kwargs: Any) -> None:
        transition_calls.append(kwargs)

    monkeypatch.setattr(setup_runtime, "has_approval_event", _no_approval)
    monkeypatch.setattr(setup_runtime, "transition_status", _record_transition)
    # Also stub the expiry check so it doesn't try to touch DB.
    monkeypatch.setattr(
        setup_runtime,
        "_check_expiry_and_invalidate",
        lambda _setup, **_kw: _async_false(),
    )

    # Long entry @ 100 with candle low=98 high=102 → entry_hit_at would fire
    # but the gate must veto.
    await setup_runtime._evaluate_setup(
        setup, high=102.0, low=98.0, close=101.0, candle_ts=CANDLE_TS
    )
    assert transition_calls == []


async def test_scout_proposal_with_approval_activates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Source=scout_proposal + has `approved` event → entry hit transitions
    to active just like a chat-initiated setup would."""
    setup = _setup(source="scout_proposal")

    async def _has_approval(*_args: Any, **_kwargs: Any) -> bool:
        return True

    transition_calls: list[dict[str, Any]] = []

    async def _record_transition(*_args: Any, **kwargs: Any) -> None:
        transition_calls.append(kwargs)

    monkeypatch.setattr(setup_runtime, "has_approval_event", _has_approval)
    monkeypatch.setattr(setup_runtime, "transition_status", _record_transition)
    monkeypatch.setattr(
        setup_runtime,
        "_check_expiry_and_invalidate",
        lambda _setup, **_kw: _async_false(),
    )
    # The runtime fires a review fire-and-forget after entry hit; stub it.
    monkeypatch.setattr(setup_runtime, "_fire_review", lambda **_kw: None)

    await setup_runtime._evaluate_setup(
        setup, high=102.0, low=98.0, close=101.0, candle_ts=CANDLE_TS
    )
    assert len(transition_calls) == 1
    assert transition_calls[0]["new_status"] == "active"
    assert transition_calls[0]["event"] == "entry_hit"


async def test_agent_proposal_bypasses_approval_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Source=agent_proposal (chat) → entry hit activates immediately even
    when no `approved` event exists. The gate only applies to scout."""
    setup = _setup(source="agent_proposal")

    approval_checked = False

    async def _track_approval(*_args: Any, **_kwargs: Any) -> bool:
        nonlocal approval_checked
        approval_checked = True
        return False  # would block IF the gate applied

    transition_calls: list[dict[str, Any]] = []

    async def _record_transition(*_args: Any, **kwargs: Any) -> None:
        transition_calls.append(kwargs)

    monkeypatch.setattr(setup_runtime, "has_approval_event", _track_approval)
    monkeypatch.setattr(setup_runtime, "transition_status", _record_transition)
    monkeypatch.setattr(
        setup_runtime,
        "_check_expiry_and_invalidate",
        lambda _setup, **_kw: _async_false(),
    )
    monkeypatch.setattr(setup_runtime, "_fire_review", lambda **_kw: None)

    await setup_runtime._evaluate_setup(
        setup, high=102.0, low=98.0, close=101.0, candle_ts=CANDLE_TS
    )
    assert approval_checked is False, (
        "approval gate must NOT be checked for agent_proposal source"
    )
    assert len(transition_calls) == 1
    assert transition_calls[0]["new_status"] == "active"


async def test_observed_cancelled_status_short_circuits_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit race-fix: between `list_open_setups()` (which read pending) and
    this evaluation tick, a concurrent /reject committed status='cancelled'.
    The new `SELECT ... FOR UPDATE` inside the runtime sees that and skips
    the transition so the setup doesn't bounce back to 'active'."""
    setup = _setup(source="agent_proposal")  # gate not relevant here

    # `raising=False` because the attribute is created on-the-fly only for
    # tests that need to override the default 'pending' returned by the stub.
    monkeypatch.setattr(
        setup_runtime, "_test_observed_status", "cancelled", raising=False
    )

    transition_calls: list[dict[str, Any]] = []

    async def _record_transition(*_args: Any, **kwargs: Any) -> None:
        transition_calls.append(kwargs)

    async def _no_approval(*_args: Any, **_kwargs: Any) -> bool:
        return True  # would otherwise pass the gate

    monkeypatch.setattr(setup_runtime, "has_approval_event", _no_approval)
    monkeypatch.setattr(setup_runtime, "transition_status", _record_transition)
    monkeypatch.setattr(
        setup_runtime,
        "_check_expiry_and_invalidate",
        lambda _setup, **_kw: _async_false(),
    )

    await setup_runtime._evaluate_setup(
        setup, high=102.0, low=98.0, close=101.0, candle_ts=CANDLE_TS
    )
    assert transition_calls == (
        []
    ), "observed cancelled status must abort the transition"


# -----------------------------------------------------------------------------
# Test helpers
# -----------------------------------------------------------------------------


async def _async_false(*_args: Any, **_kwargs: Any) -> bool:
    return False
