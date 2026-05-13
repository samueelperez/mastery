"""B.2 Paper trading — paper_fills table for ex-post slippage calibration.

Each simulated fill captured during paper trading writes a `paper_fills` row.
Rows store the intended vs. filled price, fees, and the observed slippage in
basis points, so a periodic job can recompute `SLIPPAGE_BUFFER_R[symbol]`
from p75 of observed slippage / ATR.

Indexed on `(symbol, filled_at DESC)` because the calibration job queries the
last N days per symbol.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "017"
down_revision: str | None = "016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE paper_fills (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            trade_id        uuid NOT NULL
                              REFERENCES journal_trades(id) ON DELETE CASCADE,
            user_id         text NOT NULL,
            symbol          text NOT NULL,
            timeframe       text NOT NULL,
            side            text NOT NULL CHECK (side IN ('long', 'short')),
            kind            text NOT NULL CHECK (kind IN ('entry', 'exit')),
            intended_px     numeric NOT NULL,
            filled_px       numeric NOT NULL,
            qty_pct         numeric NOT NULL CHECK (qty_pct > 0 AND qty_pct <= 1),
            spread_pct      numeric,
            atr_pct         numeric,
            slippage_bps    numeric NOT NULL,
            fee_bps         numeric NOT NULL,
            funding_bps     numeric NOT NULL DEFAULT 0,
            filled_at       timestamptz NOT NULL,
            metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at      timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_paper_fills_symbol_filled_at "
        "ON paper_fills (symbol, filled_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_paper_fills_trade "
        "ON paper_fills (trade_id, kind)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS paper_fills CASCADE")
