"""Integration tests for `app.paper_trading.positions`.

Requires a reachable Postgres with migration 024 applied. Skips cleanly if
the DB is not reachable (mirrors the pattern in `tests/integration/test_scout_smoke.py`).

Cubre los gaps identificados en la auditoría 2026-05 (paper_trading.md C5):
- Long open + close → realized PnL absoluto + fees aplicados.
- Short open + close.
- Partial close (TP parcial) + weighted reduction.
- Slippage acumulado en `slippage_usd`.
- Aislamiento multi-user (mismo trade_id imposible, mismo user/symbol OK).
- Idempotency: close_position sobre setup ya cerrado → None.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.db import session_scope
from app.paper_trading.positions import (
    close_position,
    get_balance,
    get_open_positions,
    init_balance,
    open_position,
    partial_close_position,
)


_TEST_USER_A = "paper-test-user-A"
_TEST_USER_B = "paper-test-user-B"


async def _make_trade_row(user_id: str, symbol: str, side: str) -> str:
    """Inserta una fila mínima en journal_trades para satisfacer la FK de
    paper_positions. Devuelve el trade_id como string."""
    tid = str(uuid.uuid4())
    async with session_scope() as session:
        await session.execute(
            text(
                """
                INSERT INTO journal_trades (
                    id, user_id, trade_ts, symbol, timeframe, mode, side,
                    entry_px, size, setup_tag, regime, summary_text,
                    summary_hash, status, source
                )
                VALUES (
                    CAST(:tid AS uuid), :uid, now(), :sym, '1h',
                    'manual_log', :side, 100.0, 1.0, 'test', 'range',
                    'test trade', 'h0', 'pending', 'agent_proposal'
                )
                """
            ),
            {"tid": tid, "uid": user_id, "sym": symbol, "side": side},
        )
    return tid


async def _cleanup() -> bool:
    """Wipe test data, retry up to 3× to absorb asyncpg connection cancel race."""
    import asyncio as _asyncio

    params = {"a": _TEST_USER_A, "b": _TEST_USER_B}
    for attempt in range(3):
        try:
            async with session_scope() as session:
                await session.execute(
                    text("DELETE FROM paper_equity_snapshots WHERE user_id IN (:a, :b)"),
                    params,
                )
                await session.execute(
                    text("DELETE FROM paper_positions WHERE user_id IN (:a, :b)"),
                    params,
                )
                await session.execute(
                    text("DELETE FROM paper_balance WHERE user_id IN (:a, :b)"),
                    params,
                )
                await session.execute(
                    text(
                        "DELETE FROM setup_events WHERE trade_id IN ("
                        "SELECT id FROM journal_trades WHERE user_id IN (:a, :b))"
                    ),
                    params,
                )
                await session.execute(
                    text("DELETE FROM journal_trades WHERE user_id IN (:a, :b)"),
                    params,
                )
            return True
        except Exception:
            if attempt < 2:
                await _asyncio.sleep(0.15)
                continue
            return False
    return False


async def _require_db() -> None:
    if not await _cleanup():
        pytest.skip("DB not reachable for paper trading integration test")


@pytest.fixture(autouse=True)
async def _setup_teardown() -> None:
    await _require_db()
    yield
    await _cleanup()


async def test_init_balance_idempotent() -> None:
    async with session_scope() as session:
        await init_balance(session, user_id=_TEST_USER_A, initial_usd=Decimal("10000"))
        # Second call no-ops (ON CONFLICT DO NOTHING).
        await init_balance(session, user_id=_TEST_USER_A, initial_usd=Decimal("99999"))
        bal = await get_balance(session, _TEST_USER_A)
    assert bal == Decimal("10000")


async def test_long_open_then_close_pnl_correct() -> None:
    """BTC long, entry 100, exit 110, qty 1 → +10 PnL antes de fees.
    Con 4 bps de taker fee aplicadas en ambos lados (entry 100*1*0.0004 = 0.04;
    exit 110*1*0.0004 = 0.044), realized_pnl = 10 - 0.044 = 9.956."""
    trade_id = await _make_trade_row(_TEST_USER_A, "BTCUSDT", "long")
    async with session_scope() as session:
        await init_balance(session, user_id=_TEST_USER_A, initial_usd=Decimal("10000"))
        await open_position(
            session,
            trade_id=trade_id,
            user_id=_TEST_USER_A,
            symbol="BTCUSDT",
            side="long",
            qty_coin=Decimal("1"),
            intended_entry_px=Decimal("100"),
            filled_entry_px=Decimal("100"),  # no slippage
            taker_fee_bps=4.0,
        )
    async with session_scope() as session:
        bal_after_open = await get_balance(session, _TEST_USER_A)
    # Balance descontó SOLO la fee de entry: 10000 - 0.04 = 9999.96
    assert bal_after_open == Decimal("9999.96")

    async with session_scope() as session:
        closed = await close_position(
            session,
            trade_id=trade_id,
            intended_exit_px=Decimal("110"),
            filled_exit_px=Decimal("110"),
            taker_fee_bps=4.0,
            closed_reason="tp_hit",
        )
    assert closed is not None
    assert closed.status == "closed"
    assert closed.realized_pnl_usd == Decimal("9.956")
    async with session_scope() as session:
        bal_final = await get_balance(session, _TEST_USER_A)
    # Balance += realized_pnl (que ya descuenta exit_fee): 9999.96 + 9.956 = 10009.916
    assert bal_final == Decimal("10009.916")


async def test_short_open_then_close_pnl_correct() -> None:
    """ETH short, entry 2000, exit 1800, qty 1 → +200 PnL antes de fees.
    Fees: entry 2000*1*0.0004 = 0.8; exit 1800*1*0.0004 = 0.72.
    realized_pnl = 200 - 0.72 = 199.28."""
    trade_id = await _make_trade_row(_TEST_USER_A, "ETHUSDT", "short")
    async with session_scope() as session:
        await init_balance(session, user_id=_TEST_USER_A, initial_usd=Decimal("10000"))
        await open_position(
            session,
            trade_id=trade_id,
            user_id=_TEST_USER_A,
            symbol="ETHUSDT",
            side="short",
            qty_coin=Decimal("1"),
            intended_entry_px=Decimal("2000"),
            filled_entry_px=Decimal("2000"),
            taker_fee_bps=4.0,
        )
    async with session_scope() as session:
        closed = await close_position(
            session,
            trade_id=trade_id,
            intended_exit_px=Decimal("1800"),
            filled_exit_px=Decimal("1800"),
            taker_fee_bps=4.0,
            closed_reason="tp_hit",
        )
    assert closed is not None
    assert closed.realized_pnl_usd == Decimal("199.28")


async def test_slippage_accumulated_in_position_row() -> None:
    """Slippage adverso en entry y exit acumula en slippage_usd."""
    trade_id = await _make_trade_row(_TEST_USER_A, "SOLUSDT", "long")
    async with session_scope() as session:
        await init_balance(session, user_id=_TEST_USER_A, initial_usd=Decimal("10000"))
        # Long entry: fill por encima del intended (peor).
        pos = await open_position(
            session,
            trade_id=trade_id,
            user_id=_TEST_USER_A,
            symbol="SOLUSDT",
            side="long",
            qty_coin=Decimal("10"),
            intended_entry_px=Decimal("100"),
            filled_entry_px=Decimal("100.05"),  # 0.05 worse * 10 = 0.5 slip
            taker_fee_bps=4.0,
        )
    assert pos.slippage_usd == Decimal("0.5")

    async with session_scope() as session:
        closed = await close_position(
            session,
            trade_id=trade_id,
            intended_exit_px=Decimal("110"),
            filled_exit_px=Decimal("109.90"),  # 0.10 worse * 10 = 1.0 slip
            taker_fee_bps=4.0,
            closed_reason="tp_hit",
        )
    assert closed is not None
    assert closed.slippage_usd == Decimal("1.5")  # entry 0.5 + exit 1.0


async def test_partial_close_reduces_qty_and_pnl() -> None:
    """Long 1 BTC @ 100. Cierra 50% @ 110 → 0.5 BTC remaining + realized partial.
    Partial PnL = (110-100) * 0.5 - exit_fee(110*0.5*0.0004=0.022) = 5 - 0.022 = 4.978."""
    trade_id = await _make_trade_row(_TEST_USER_A, "BTCUSDT", "long")
    async with session_scope() as session:
        await init_balance(session, user_id=_TEST_USER_A, initial_usd=Decimal("10000"))
        await open_position(
            session,
            trade_id=trade_id,
            user_id=_TEST_USER_A,
            symbol="BTCUSDT",
            side="long",
            qty_coin=Decimal("1"),
            intended_entry_px=Decimal("100"),
            filled_entry_px=Decimal("100"),
            taker_fee_bps=4.0,
        )
    async with session_scope() as session:
        updated = await partial_close_position(
            session,
            trade_id=trade_id,
            qty_frac_closed=Decimal("0.5"),
            intended_exit_px=Decimal("110"),
            filled_exit_px=Decimal("110"),
            taker_fee_bps=4.0,
        )
    assert updated is not None
    assert updated.status == "open"  # parcial — sigue abierta
    assert updated.qty_coin == Decimal("0.5")
    assert updated.realized_pnl_usd == Decimal("4.978")


async def test_close_idempotent_returns_none() -> None:
    """Second close on the same trade_id returns None (no open row to update)."""
    trade_id = await _make_trade_row(_TEST_USER_A, "BTCUSDT", "long")
    async with session_scope() as session:
        await init_balance(session, user_id=_TEST_USER_A, initial_usd=Decimal("10000"))
        await open_position(
            session,
            trade_id=trade_id,
            user_id=_TEST_USER_A,
            symbol="BTCUSDT",
            side="long",
            qty_coin=Decimal("1"),
            intended_entry_px=Decimal("100"),
            filled_entry_px=Decimal("100"),
            taker_fee_bps=4.0,
        )
    async with session_scope() as session:
        first = await close_position(
            session,
            trade_id=trade_id,
            intended_exit_px=Decimal("110"),
            filled_exit_px=Decimal("110"),
            taker_fee_bps=4.0,
            closed_reason="tp_hit",
        )
    assert first is not None
    async with session_scope() as session:
        second = await close_position(
            session,
            trade_id=trade_id,
            intended_exit_px=Decimal("120"),
            filled_exit_px=Decimal("120"),
            taker_fee_bps=4.0,
            closed_reason="tp_hit",
        )
    assert second is None


async def test_multi_user_isolation() -> None:
    """User A y user B abren misma BTCUSDT long — sus positions/balance
    quedan aislados por user_id en queries."""
    trade_a = await _make_trade_row(_TEST_USER_A, "BTCUSDT", "long")
    trade_b = await _make_trade_row(_TEST_USER_B, "BTCUSDT", "long")
    async with session_scope() as session:
        await init_balance(session, user_id=_TEST_USER_A, initial_usd=Decimal("10000"))
        await init_balance(session, user_id=_TEST_USER_B, initial_usd=Decimal("5000"))
        await open_position(
            session,
            trade_id=trade_a,
            user_id=_TEST_USER_A,
            symbol="BTCUSDT",
            side="long",
            qty_coin=Decimal("1"),
            intended_entry_px=Decimal("100"),
            filled_entry_px=Decimal("100"),
            taker_fee_bps=4.0,
        )
        await open_position(
            session,
            trade_id=trade_b,
            user_id=_TEST_USER_B,
            symbol="BTCUSDT",
            side="long",
            qty_coin=Decimal("1"),
            intended_entry_px=Decimal("100"),
            filled_entry_px=Decimal("100"),
            taker_fee_bps=4.0,
        )
    async with session_scope() as session:
        open_a = await get_open_positions(session, user_id=_TEST_USER_A)
        open_b = await get_open_positions(session, user_id=_TEST_USER_B)
    assert len(open_a) == 1
    assert len(open_b) == 1
    assert open_a[0].user_id == _TEST_USER_A
    assert open_b[0].user_id == _TEST_USER_B
