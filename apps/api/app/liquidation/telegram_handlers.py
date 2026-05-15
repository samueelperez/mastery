"""Telegram callback handlers for ground-truth collection.

Invoked from `notifications/routes.py::_handle_callback` when a callback_data
starting with `gt:` is received. Resolves the setup, extracts the heatmap
citation snapshot, and persists a row to `liquidation_agreement_log`.

NOTE on factor_snapshot: this handler expects `journal_trades.factor_snapshot`
jsonb to contain a `get_liquidation_heatmap` key with the citation's
snapshot (current_price, nearest_*_liq_price, source_breakdown_*_price,
timeframe, sources_agreement). Production persistence of that key into
factor_snapshot is the responsibility of the upstream `factor_snapshot`
builder; until that lands, the handler logs `gt_no_heatmap_citation` and
returns False without crashing.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

from app.liquidation.models import TDVerdict

LOG = logging.getLogger(__name__)


async def record_ground_truth(
    *,
    session_factory: Any,
    user_id: str,
    setup_id: str,
    verdict: str,
) -> bool:
    """Persist a ground-truth verdict from the operator.

    Args:
        session_factory: callable returning an async session context manager
            (matches AgentDeps.session_factory and module-level session_scope).
        user_id: scoped user.
        setup_id: UUID of the setup the verdict refers to.
        verdict: 'agree' | 'close' | 'disagree'. 'skipped' is inferred by
            absence (timeout); never written from this handler.

    Returns True on insert; False on:
      - invalid verdict
      - setup not found
      - setup row has no `get_liquidation_heatmap` in factor_snapshot
      - citation snapshot has no nearest_*_liq_price (no zone to record)
    """
    valid: tuple[TDVerdict, ...] = ("agree", "close", "disagree")
    if verdict not in valid:
        LOG.warning("invalid_gt_verdict", extra={"verdict": verdict})
        return False

    async with session_factory() as session:
        row = await session.execute(
            text(
                """
                SELECT symbol, factor_snapshot
                FROM journal_trades
                WHERE id = :setup_id AND user_id = :user_id
                """
            ),
            {"setup_id": setup_id, "user_id": user_id},
        )
        setup = row.first()
        if not setup:
            LOG.warning("gt_setup_not_found", extra={"setup_id": setup_id})
            return False

        fs = setup.factor_snapshot or {}
        liq = fs.get("get_liquidation_heatmap") or {}
        if not liq:
            LOG.warning("gt_no_heatmap_citation", extra={"setup_id": setup_id})
            return False

        proposed_price = liq.get("nearest_short_liq_price") or liq.get("nearest_long_liq_price")
        proposed_side = "short_liq" if liq.get("nearest_short_liq_price") else "long_liq"
        if proposed_price is None:
            LOG.warning("gt_no_proposed_zone", extra={"setup_id": setup_id})
            return False

        source_a_price = liq.get("source_breakdown_a_price")
        source_b_price = liq.get("source_breakdown_b_price")
        timeframe = liq.get("timeframe") or "4h"

        delta_a = (
            abs(float(source_a_price) - float(proposed_price)) / float(proposed_price) * 100
            if source_a_price is not None
            else None
        )
        delta_b = (
            abs(float(source_b_price) - float(proposed_price)) / float(proposed_price) * 100
            if source_b_price is not None
            else None
        )

        await session.execute(
            text(
                """
                INSERT INTO liquidation_agreement_log (
                    user_id, setup_id, symbol, timeframe,
                    proposed_zone_price, proposed_zone_side,
                    source_a_price, source_b_price, source_c_verdict,
                    delta_a_pct, delta_b_pct
                )
                VALUES (
                    :user_id, :setup_id, :symbol, :timeframe,
                    :proposed_price, :proposed_side,
                    :source_a_price, :source_b_price, :verdict,
                    :delta_a, :delta_b
                )
                """
            ),
            {
                "user_id": user_id,
                "setup_id": setup_id,
                "symbol": setup.symbol,
                "timeframe": timeframe,
                "proposed_price": proposed_price,
                "proposed_side": proposed_side,
                "source_a_price": source_a_price,
                "source_b_price": source_b_price,
                "verdict": verdict,
                "delta_a": delta_a,
                "delta_b": delta_b,
            },
        )
        await session.commit()
        LOG.info(
            "gt_recorded",
            extra={
                "setup_id": setup_id,
                "verdict": verdict,
                "delta_a": delta_a,
                "delta_b": delta_b,
            },
        )
        return True
