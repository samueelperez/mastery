"""F4 — paper trading engine tables.

Adds `paper_balance`, `paper_positions`, `paper_equity_snapshots`.

See `docs/adr/0001-paper-trading-engine.md`. Existing `paper_fills` (017) is
kept for slippage calibration and remains independent of this engine.

All monetary columns use `numeric` (arbitrary precision) — Python side uses
`decimal.Decimal`. The `paper_trading/engine.py::simulate_fill` stays in
`float` because its scale (bps) is non-monetary.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "024"
down_revision: str | None = "023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # paper_balance: una fila por user. Initial + current.
    op.execute(
        """
        CREATE TABLE paper_balance (
            user_id          text PRIMARY KEY,
            initial_usd      numeric NOT NULL CHECK (initial_usd > 0),
            current_usd      numeric NOT NULL,
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    # paper_positions: una fila por setup. status='open' mientras hay qty
    # restante; status='closed' tras último fill de salida. FK CASCADE a
    # journal_trades.
    op.execute(
        """
        CREATE TABLE paper_positions (
            id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            trade_id                 uuid NOT NULL REFERENCES journal_trades(id) ON DELETE CASCADE,
            user_id                  text NOT NULL,
            symbol                   text NOT NULL,
            side                     text NOT NULL CHECK (side IN ('long','short')),
            qty_coin                 numeric NOT NULL CHECK (qty_coin >= 0),
            avg_entry_px             numeric NOT NULL CHECK (avg_entry_px > 0),
            notional_usd_at_entry    numeric NOT NULL,
            realized_pnl_usd         numeric NOT NULL DEFAULT 0,
            fees_paid_usd            numeric NOT NULL DEFAULT 0,
            slippage_usd             numeric NOT NULL DEFAULT 0,
            status                   text NOT NULL CHECK (status IN ('open','closed')),
            opened_at                timestamptz NOT NULL DEFAULT now(),
            closed_at                timestamptz,
            closed_reason            text,
            updated_at               timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX paper_positions_user_status_idx "
        "ON paper_positions (user_id, status, opened_at DESC)"
    )
    op.execute(
        "CREATE UNIQUE INDEX paper_positions_one_open_per_trade_idx "
        "ON paper_positions (trade_id) "
        "WHERE status = 'open'"
    )

    # paper_equity_snapshots: time-series por user. Una fila cada N minutos
    # (configurable). Index para listar la curva por user.
    op.execute(
        """
        CREATE TABLE paper_equity_snapshots (
            id              bigserial PRIMARY KEY,
            user_id         text NOT NULL,
            ts              timestamptz NOT NULL,
            balance_usd     numeric NOT NULL,
            unrealized_usd  numeric NOT NULL DEFAULT 0,
            equity_usd      numeric NOT NULL,
            n_open_positions int NOT NULL DEFAULT 0
        )
        """
    )
    op.execute(
        "CREATE INDEX paper_equity_user_ts_idx "
        "ON paper_equity_snapshots (user_id, ts DESC)"
    )
    # Anti-dup: misma marca de tiempo por user es un no-op (snapshot idempotente).
    op.execute(
        "CREATE UNIQUE INDEX paper_equity_user_ts_unique "
        "ON paper_equity_snapshots (user_id, ts)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS paper_equity_snapshots CASCADE")
    op.execute("DROP TABLE IF EXISTS paper_positions CASCADE")
    op.execute("DROP TABLE IF EXISTS paper_balance CASCADE")
