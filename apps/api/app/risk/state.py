"""Portfolio state fetchers for the system-state risk gates.

The gates themselves live in ``gates.py`` and are pure; the inputs they
need (equity, HWM, realized PnL window, gross leverage) come from the
DB. Keeping the DB-touching code here lets us mock the fetchers in
isolation and keeps the gate logic 100% testable without fixtures.

All queries scope by ``user_id`` (single-user system today but defense
in depth — see project invariant #2).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class PortfolioSnapshot:
    """The slice of portfolio state the gates need to make a decision.

    Attributes:
        equity_usd: latest entry in ``paper_equity_snapshots``; 0.0 when
            the user has not yet started paper trading.
        high_watermark_usd: max ``equity_usd`` over the rolling window
            (currently the full snapshot history per user). 0.0 if no
            snapshots exist yet — the drawdown gate then no-ops.
        realized_pnl_last_24h_usd: sum of closed ``paper_positions.realized_pnl_usd``
            where ``closed_at >= now() - 24h``. Negative when net loss.
        n_positions_open: number of currently-open paper positions.
            Currently informational; future use in the gross-leverage gate.
    """

    equity_usd: float
    high_watermark_usd: float
    realized_pnl_last_24h_usd: float
    n_positions_open: int


async def fetch_portfolio_snapshot(
    session: AsyncSession, *, user_id: str
) -> PortfolioSnapshot:
    """One round-trip per call: three lightweight aggregates against
    ``paper_equity_snapshots`` and ``paper_positions``. Returns a
    snapshot with zeros for users who have not yet paper-traded — the
    gates' ``skipped`` semantics handle that gracefully.
    """
    row = (
        await session.execute(
            text(
                """
                WITH
                  latest_eq AS (
                    SELECT equity_usd, MAX(equity_usd) OVER ()::float AS hwm
                    FROM paper_equity_snapshots
                    WHERE user_id = :uid
                    ORDER BY ts DESC
                    LIMIT 1
                  ),
                  pnl_24h AS (
                    SELECT COALESCE(SUM(realized_pnl_usd), 0)::float AS pnl
                    FROM paper_positions
                    WHERE user_id = :uid
                      AND status = 'closed'
                      AND closed_at >= now() - interval '24 hours'
                  ),
                  open_count AS (
                    SELECT COUNT(*)::int AS n
                    FROM paper_positions
                    WHERE user_id = :uid AND status = 'open'
                  )
                SELECT
                  COALESCE((SELECT equity_usd::float FROM latest_eq), 0.0) AS equity,
                  COALESCE((SELECT hwm::float FROM latest_eq), 0.0) AS hwm,
                  (SELECT pnl FROM pnl_24h) AS pnl_24h,
                  (SELECT n FROM open_count) AS open_n
                """
            ),
            {"uid": user_id},
        )
    ).mappings().one()

    return PortfolioSnapshot(
        equity_usd=float(row["equity"]),
        high_watermark_usd=float(row["hwm"]),
        realized_pnl_last_24h_usd=float(row["pnl_24h"]),
        n_positions_open=int(row["open_n"]),
    )
