"""F4 — paper trading positions, balance and equity snapshots.

ADR: `docs/adr/0001-paper-trading-engine.md`.

All money uses `decimal.Decimal` with 28-digit precision. Slippage bps and
fee bps come in as `float` from `engine.py::simulate_fill` (its scale is bps,
not money) and are converted to `Decimal` here before applying to notional.

API surface (sync-async split):
- `init_balance` — call once per user when they first opt in to paper trading.
- `open_position` — at `entry_hit`. Applies entry fee + slippage to balance.
- `close_position` — at `sl_hit` / `tp_hit final` / `time_stopped`. Computes
  realized PnL, deducts exit fee, updates balance.
- `partial_close_position` — at `tp_hit partial`. Reduces qty + bumps
  realized_pnl_usd; balance updated proportionally.
- `get_balance` / `get_open_positions` / `get_equity_curve` — read-side.
- `snapshot_equity` — periodic mark-to-market (caller passes mark prices).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal, getcontext
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Money precision. Decimal default (28) is enough for crypto USD-denominated
# computations (max realistic notional ≈ 1e12 with 8 decimal places of
# precision). ROUND_HALF_EVEN is "banker's rounding" — IEEE-754 default and
# what Postgres `numeric` uses by default.
getcontext().prec = 28
getcontext().rounding = ROUND_HALF_EVEN

_BPS = Decimal("10000")  # 1 bps = 1 / 10_000


@dataclass(frozen=True)
class PaperPositionRow:
    id: str
    trade_id: str
    user_id: str
    symbol: str
    side: str
    qty_coin: Decimal
    avg_entry_px: Decimal
    notional_usd_at_entry: Decimal
    realized_pnl_usd: Decimal
    fees_paid_usd: Decimal
    slippage_usd: Decimal
    status: str
    opened_at: datetime
    closed_at: datetime | None
    closed_reason: str | None


def _to_dec(x: float | int | Decimal | str | None) -> Decimal:
    """Coerce to Decimal via str to avoid binary-float artifacts."""
    if x is None:
        return Decimal("0")
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


def _row_to_position(r: dict[str, Any]) -> PaperPositionRow:
    return PaperPositionRow(
        id=str(r["id"]),
        trade_id=str(r["trade_id"]),
        user_id=r["user_id"],
        symbol=r["symbol"],
        side=r["side"],
        qty_coin=_to_dec(r["qty_coin"]),
        avg_entry_px=_to_dec(r["avg_entry_px"]),
        notional_usd_at_entry=_to_dec(r["notional_usd_at_entry"]),
        realized_pnl_usd=_to_dec(r["realized_pnl_usd"]),
        fees_paid_usd=_to_dec(r["fees_paid_usd"]),
        slippage_usd=_to_dec(r["slippage_usd"]),
        status=r["status"],
        opened_at=r["opened_at"],
        closed_at=r["closed_at"],
        closed_reason=r["closed_reason"],
    )


# ---------------------------------------------------------------------------
# Balance
# ---------------------------------------------------------------------------


async def init_balance(
    session: AsyncSession, *, user_id: str, initial_usd: Decimal
) -> None:
    """Idempotent: si el user ya tiene balance, no-op."""
    if initial_usd <= 0:
        raise ValueError("initial_usd must be > 0")
    await session.execute(
        text(
            """
            INSERT INTO paper_balance (user_id, initial_usd, current_usd)
            VALUES (:uid, :init, :init)
            ON CONFLICT (user_id) DO NOTHING
            """
        ),
        {"uid": user_id, "init": str(initial_usd)},
    )


async def get_balance(session: AsyncSession, user_id: str) -> Decimal | None:
    row = (
        await session.execute(
            text(
                "SELECT current_usd FROM paper_balance WHERE user_id = :uid"
            ),
            {"uid": user_id},
        )
    ).scalar_one_or_none()
    return _to_dec(row) if row is not None else None


async def _bump_balance(
    session: AsyncSession, *, user_id: str, delta_usd: Decimal
) -> None:
    """Add delta_usd to the user's current balance (can be negative)."""
    await session.execute(
        text(
            """
            UPDATE paper_balance
            SET current_usd = current_usd + :delta,
                updated_at = now()
            WHERE user_id = :uid
            """
        ),
        {"uid": user_id, "delta": str(delta_usd)},
    )


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------


async def open_position(
    session: AsyncSession,
    *,
    trade_id: str,
    user_id: str,
    symbol: str,
    side: str,
    qty_coin: Decimal,
    intended_entry_px: Decimal,
    filled_entry_px: Decimal,
    taker_fee_bps: float,
) -> PaperPositionRow:
    """Abre una posición. Calcula slippage = (filled - intended) × qty,
    fee = filled × qty × fee_bps/10000, descuenta ambos del balance.

    `qty_coin` debe ser positiva (incluso para short — el side codifica
    la dirección). El caller decide el sizing.
    """
    if qty_coin <= 0 or filled_entry_px <= 0 or intended_entry_px <= 0:
        raise ValueError("qty_coin and prices must be > 0")
    if side not in ("long", "short"):
        raise ValueError("side must be 'long' or 'short'")

    notional = filled_entry_px * qty_coin
    fee = notional * _to_dec(taker_fee_bps) / _BPS
    # Slippage cost = direction-aware diff × qty. Entry slippage always
    # adverse (worse price): long pays more, short receives less.
    if side == "long":
        slip = (filled_entry_px - intended_entry_px) * qty_coin
    else:
        slip = (intended_entry_px - filled_entry_px) * qty_coin
    if slip < 0:
        slip = Decimal("0")  # entry slippage by construction non-negative

    row = (
        await session.execute(
            text(
                """
                INSERT INTO paper_positions (
                    trade_id, user_id, symbol, side, qty_coin, avg_entry_px,
                    notional_usd_at_entry, fees_paid_usd, slippage_usd, status
                ) VALUES (
                    CAST(:tid AS uuid), :uid, :sym, :side, :qty, :avg,
                    :notional, :fee, :slip, 'open'
                )
                RETURNING id::text AS id, trade_id::text AS trade_id, user_id,
                          symbol, side, qty_coin, avg_entry_px,
                          notional_usd_at_entry, realized_pnl_usd,
                          fees_paid_usd, slippage_usd, status, opened_at,
                          closed_at, closed_reason
                """
            ),
            {
                "tid": trade_id,
                "uid": user_id,
                "sym": symbol.upper(),
                "side": side,
                "qty": str(qty_coin),
                "avg": str(filled_entry_px),
                "notional": str(notional),
                "fee": str(fee),
                "slip": str(slip),
            },
        )
    ).mappings().one()

    # Balance se descuenta solo por la fee (notional no se "gasta" — es
    # exposición de margin, no salida de caja para perps). En spot real
    # sí saldría el notional; aquí modelamos perps.
    await _bump_balance(session, user_id=user_id, delta_usd=-fee)
    return _row_to_position(dict(row))


async def close_position(
    session: AsyncSession,
    *,
    trade_id: str,
    intended_exit_px: Decimal,
    filled_exit_px: Decimal,
    taker_fee_bps: float,
    closed_reason: str,
) -> PaperPositionRow | None:
    """Cierra la posición abierta de un trade. Calcula realized PnL,
    descuenta fee de salida, suma realized + recover-de-fees al balance.

    Idempotent: si ya estaba `closed`, devuelve None.
    """
    # Read + lock the row.
    open_row = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, trade_id::text AS trade_id, user_id,
                       symbol, side, qty_coin, avg_entry_px,
                       notional_usd_at_entry, realized_pnl_usd,
                       fees_paid_usd, slippage_usd, status, opened_at,
                       closed_at, closed_reason
                FROM paper_positions
                WHERE trade_id = CAST(:tid AS uuid) AND status = 'open'
                FOR UPDATE
                """
            ),
            {"tid": trade_id},
        )
    ).mappings().one_or_none()
    if open_row is None:
        return None

    pos = _row_to_position(dict(open_row))
    qty = pos.qty_coin
    avg_entry = pos.avg_entry_px
    side = pos.side

    exit_notional = filled_exit_px * qty
    exit_fee = exit_notional * _to_dec(taker_fee_bps) / _BPS
    if side == "long":
        realized_pnl = (filled_exit_px - avg_entry) * qty - exit_fee
        exit_slip = (intended_exit_px - filled_exit_px) * qty
    else:
        realized_pnl = (avg_entry - filled_exit_px) * qty - exit_fee
        exit_slip = (filled_exit_px - intended_exit_px) * qty
    if exit_slip < 0:
        exit_slip = Decimal("0")

    closed_row = (
        await session.execute(
            text(
                """
                UPDATE paper_positions
                SET qty_coin = 0,
                    realized_pnl_usd = realized_pnl_usd + :pnl,
                    fees_paid_usd = fees_paid_usd + :fee,
                    slippage_usd = slippage_usd + :slip,
                    status = 'closed',
                    closed_at = now(),
                    closed_reason = :reason,
                    updated_at = now()
                WHERE id = CAST(:pid AS uuid)
                RETURNING id::text AS id, trade_id::text AS trade_id, user_id,
                          symbol, side, qty_coin, avg_entry_px,
                          notional_usd_at_entry, realized_pnl_usd,
                          fees_paid_usd, slippage_usd, status, opened_at,
                          closed_at, closed_reason
                """
            ),
            {
                "pid": pos.id,
                "pnl": str(realized_pnl),
                "fee": str(exit_fee),
                "slip": str(exit_slip),
                "reason": closed_reason,
            },
        )
    ).mappings().one()

    # Balance += realized_pnl (que ya descuenta exit_fee).
    await _bump_balance(session, user_id=pos.user_id, delta_usd=realized_pnl)
    return _row_to_position(dict(closed_row))


async def partial_close_position(
    session: AsyncSession,
    *,
    trade_id: str,
    qty_frac_closed: Decimal,
    intended_exit_px: Decimal,
    filled_exit_px: Decimal,
    taker_fee_bps: float,
) -> PaperPositionRow | None:
    """Cierra una fracción ∈ (0, 1) de la posición (p.ej. TP parcial al 50%).

    No marca status='closed' — mantiene la posición abierta con qty reducida.
    Acumula realized_pnl y fees en la misma fila.
    """
    if not (Decimal("0") < qty_frac_closed < Decimal("1")):
        raise ValueError("qty_frac_closed must be in (0, 1)")
    open_row = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, trade_id::text AS trade_id, user_id,
                       symbol, side, qty_coin, avg_entry_px,
                       notional_usd_at_entry, realized_pnl_usd,
                       fees_paid_usd, slippage_usd, status, opened_at,
                       closed_at, closed_reason
                FROM paper_positions
                WHERE trade_id = CAST(:tid AS uuid) AND status = 'open'
                FOR UPDATE
                """
            ),
            {"tid": trade_id},
        )
    ).mappings().one_or_none()
    if open_row is None:
        return None
    pos = _row_to_position(dict(open_row))
    qty_to_close = pos.qty_coin * qty_frac_closed
    remaining_qty = pos.qty_coin - qty_to_close

    exit_notional = filled_exit_px * qty_to_close
    exit_fee = exit_notional * _to_dec(taker_fee_bps) / _BPS
    if pos.side == "long":
        partial_pnl = (filled_exit_px - pos.avg_entry_px) * qty_to_close - exit_fee
        partial_slip = (intended_exit_px - filled_exit_px) * qty_to_close
    else:
        partial_pnl = (pos.avg_entry_px - filled_exit_px) * qty_to_close - exit_fee
        partial_slip = (filled_exit_px - intended_exit_px) * qty_to_close
    if partial_slip < 0:
        partial_slip = Decimal("0")

    updated_row = (
        await session.execute(
            text(
                """
                UPDATE paper_positions
                SET qty_coin = :remaining,
                    realized_pnl_usd = realized_pnl_usd + :pnl,
                    fees_paid_usd = fees_paid_usd + :fee,
                    slippage_usd = slippage_usd + :slip,
                    updated_at = now()
                WHERE id = CAST(:pid AS uuid)
                RETURNING id::text AS id, trade_id::text AS trade_id, user_id,
                          symbol, side, qty_coin, avg_entry_px,
                          notional_usd_at_entry, realized_pnl_usd,
                          fees_paid_usd, slippage_usd, status, opened_at,
                          closed_at, closed_reason
                """
            ),
            {
                "pid": pos.id,
                "remaining": str(remaining_qty),
                "pnl": str(partial_pnl),
                "fee": str(exit_fee),
                "slip": str(partial_slip),
            },
        )
    ).mappings().one()

    await _bump_balance(session, user_id=pos.user_id, delta_usd=partial_pnl)
    return _row_to_position(dict(updated_row))


async def get_open_positions(
    session: AsyncSession, *, user_id: str
) -> list[PaperPositionRow]:
    rows = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, trade_id::text AS trade_id, user_id,
                       symbol, side, qty_coin, avg_entry_px,
                       notional_usd_at_entry, realized_pnl_usd,
                       fees_paid_usd, slippage_usd, status, opened_at,
                       closed_at, closed_reason
                FROM paper_positions
                WHERE user_id = :uid AND status = 'open'
                ORDER BY opened_at DESC
                """
            ),
            {"uid": user_id},
        )
    ).mappings().all()
    return [_row_to_position(dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# Equity snapshots
# ---------------------------------------------------------------------------


def _unrealized_for_position(
    pos: PaperPositionRow, mark_px: Decimal
) -> Decimal:
    if pos.side == "long":
        return (mark_px - pos.avg_entry_px) * pos.qty_coin
    return (pos.avg_entry_px - mark_px) * pos.qty_coin


async def snapshot_equity(
    session: AsyncSession,
    *,
    user_id: str,
    mark_prices: dict[str, Decimal],
    ts: datetime | None = None,
) -> Decimal:
    """Mark-to-market all open positions and persist a snapshot.

    Returns the equity (balance + unrealized) for the user. Idempotent
    on (user_id, ts) — the UNIQUE index does the dedup.
    """
    ts = ts or datetime.now(tz=UTC)
    balance = await get_balance(session, user_id) or Decimal("0")
    positions = await get_open_positions(session, user_id=user_id)
    unrealized = Decimal("0")
    for pos in positions:
        mark = mark_prices.get(pos.symbol.upper())
        if mark is None:
            continue
        unrealized += _unrealized_for_position(pos, mark)
    equity = balance + unrealized

    await session.execute(
        text(
            """
            INSERT INTO paper_equity_snapshots (
                user_id, ts, balance_usd, unrealized_usd, equity_usd,
                n_open_positions
            ) VALUES (:uid, :ts, :bal, :unr, :eq, :n)
            ON CONFLICT (user_id, ts) DO NOTHING
            """
        ),
        {
            "uid": user_id,
            "ts": ts,
            "bal": str(balance),
            "unr": str(unrealized),
            "eq": str(equity),
            "n": len(positions),
        },
    )
    return equity


async def get_equity_curve(
    session: AsyncSession,
    *,
    user_id: str,
    since: datetime,
    until: datetime,
    limit: int = 5000,
) -> list[tuple[datetime, Decimal]]:
    rows = (
        await session.execute(
            text(
                """
                SELECT ts, equity_usd
                FROM paper_equity_snapshots
                WHERE user_id = :uid AND ts >= :since AND ts <= :until
                ORDER BY ts ASC
                LIMIT :lim
                """
            ),
            {"uid": user_id, "since": since, "until": until, "lim": limit},
        )
    ).mappings().all()
    return [(r["ts"], _to_dec(r["equity_usd"])) for r in rows]


__all__ = [
    "PaperPositionRow",
    "close_position",
    "get_balance",
    "get_equity_curve",
    "get_open_positions",
    "init_balance",
    "open_position",
    "partial_close_position",
    "snapshot_equity",
]
