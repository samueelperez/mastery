"""Verify scout dispatcher honours the portfolio-state risk gates (RM-3).

The dispatcher's new pre-LLM step (between rate limits and agent invocation)
queries portfolio state and short-circuits with a structured drop reason
when ``daily_loss_gate`` or ``max_drawdown_gate`` rejects. We monkeypatch
``fetch_portfolio_snapshot`` so these unit tests don't require a real DB.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("OPENROUTER_API_KEY", "sk-test-placeholder")

import app.setups.scout_dispatcher as scout_dispatcher
from app.risk.state import PortfolioSnapshot


@pytest.fixture(autouse=True)
def _stub_db_and_agent(monkeypatch: pytest.MonkeyPatch):
    """Stub everything the dispatcher would normally hit so the only thing
    under test is the gate logic."""

    # Cooldown / rate limit / session_scope all stubbed to no-ops.
    async def _no_pause(*_args, **_kwargs):
        from app.alerts.cooldown import CooldownVerdict

        return CooldownVerdict(
            paused=False,
            reason="ok",
            ends_at=None,
            consecutive_losses=0,
            scope="symbol",
        )

    async def _zero_count(*_args, **_kwargs):
        return 0

    monkeypatch.setattr(scout_dispatcher, "should_pause_scout", _no_pause)
    monkeypatch.setattr(
        scout_dispatcher, "_count_active_setups_for_symbol", _zero_count
    )
    monkeypatch.setattr(scout_dispatcher, "_count_proposals_in_last_24h", _zero_count)

    # Replace `session_scope` with a context manager that yields a dummy
    # session — the gates layer never inspects it because we also stub
    # `fetch_portfolio_snapshot`.
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _scope():
        yield object()

    monkeypatch.setattr(scout_dispatcher, "session_scope", _scope)

    # Default snapshot — no positions, no losses, no equity. Tests override
    # this by re-stubbing `fetch_portfolio_snapshot` per case.
    monkeypatch.setattr(
        scout_dispatcher,
        "fetch_portfolio_snapshot",
        AsyncMock(
            return_value=PortfolioSnapshot(
                equity_usd=0.0,
                high_watermark_usd=0.0,
                realized_pnl_last_24h_usd=0.0,
                n_positions_open=0,
            )
        ),
    )

    # Stub the scout agent so the dispatcher never tries to call OpenRouter.
    class _NeverInvoked:
        async def run(self, *_a, **_k):  # pragma: no cover
            raise AssertionError(
                "scout agent should NOT be invoked when a portfolio gate fails"
            )

    monkeypatch.setattr(
        scout_dispatcher, "get_scout_agent", lambda: _NeverInvoked()
    )


def _call_dispatch():
    """Standard dispatch invocation reused by each gate-failure test."""
    import asyncio

    return asyncio.run(
        scout_dispatcher.dispatch_scout_match(
            user_id="test-user",
            rule_id="rule-1",
            rule_name="rsi_oversold",
            symbol="BTCUSDT",
            timeframe="4h",
            snapshot={"rsi": 25, "close": 80_000.0},
            fired_at=datetime.now(tz=UTC),
        )
    )


def test_drawdown_circuit_drops_before_llm(monkeypatch: pytest.MonkeyPatch):
    """Equity below HWM by more than max_drawdown_circuit_pct → drop."""
    monkeypatch.setattr(
        scout_dispatcher,
        "fetch_portfolio_snapshot",
        AsyncMock(
            return_value=PortfolioSnapshot(
                equity_usd=8_500.0,
                high_watermark_usd=10_000.0,
                realized_pnl_last_24h_usd=0.0,
                n_positions_open=0,
            )
        ),
    )
    verdict = _call_dispatch()
    assert verdict.accepted is False
    assert verdict.drop_reason == "drawdown_circuit"
    assert "manual unlock required" in (verdict.detail or "")


def test_daily_loss_freeze_drops_before_llm(monkeypatch: pytest.MonkeyPatch):
    """24h realized loss ≥ daily_loss_limit_pct of equity → drop."""
    monkeypatch.setattr(
        scout_dispatcher,
        "fetch_portfolio_snapshot",
        AsyncMock(
            return_value=PortfolioSnapshot(
                equity_usd=10_000.0,
                high_watermark_usd=10_000.0,
                realized_pnl_last_24h_usd=-400.0,  # 4% loss
                n_positions_open=0,
            )
        ),
    )
    verdict = _call_dispatch()
    assert verdict.accepted is False
    assert verdict.drop_reason == "daily_loss_freeze"


def test_zero_equity_skips_portfolio_gates(monkeypatch: pytest.MonkeyPatch):
    """Brand-new user with no paper trades yet: equity_usd == 0 → gates
    skipped; dispatch proceeds past the portfolio block (then drops on
    `validator_raised` because the stub agent raises)."""
    # The default snapshot in the fixture already has equity_usd=0; just
    # verify the gates don't fire here.
    verdict = _call_dispatch()
    assert verdict.drop_reason not in {"drawdown_circuit", "daily_loss_freeze"}
