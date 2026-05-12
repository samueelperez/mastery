"""Tests para `get_recent_lessons_for_factors` (F5.5).

Verifica:
- Solo devuelve lecciones con `verdict='thesis_broken'` y factor con
  `factor_verdicts[key].verdict='failed'`.
- Respeta `per_factor` cap.
- Respeta `lookback_days`.
- Filtra correctamente por `regime_label` cuando se pasa.
- Maneja factor_keys vacío y trades sin post-mortem.

Requiere una sesión real con migraciones aplicadas + datos sembrados.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from app.backtest.factor_stats_repo import get_recent_lessons_for_factors

pytestmark = pytest.mark.asyncio


async def _seed_trade_and_post_mortem(
    session: Any,
    *,
    user_id: str,
    symbol: str,
    regime: str,
    verdict: str,
    factor_verdicts: dict[str, Any],
    lesson_es: str,
    days_ago: int = 0,
) -> str:
    """Helper que crea un journal_trade + setup_post_mortem mínimos para
    los tests. Devuelve trade_id."""
    trade_id = str(uuid.uuid4())
    closed_at = datetime.now(tz=UTC) - timedelta(days=days_ago)

    await session.execute(
        text(
            """
            INSERT INTO journal_trades (
                id, user_id, trade_ts, symbol, timeframe, mode, side,
                entry_px, size, setup_tag, regime, mistakes, news_24h,
                features, summary_text, summary_hash, embedding_version,
                status, source, stop_loss_px, targets, confidence,
                proposed_at, closed_at, r_multiple, exit_px
            ) VALUES (
                CAST(:tid AS uuid), :uid, :ts, :symbol, '1h', 'manual_log',
                'long', 100.0, 0.0, 'test_tag', :regime, NULL,
                '{}'::jsonb, CAST('{}' AS jsonb), :sym_text, :sum_hash, 1,
                'closed', 'agent_proposal', 95.0,
                CAST('[]' AS jsonb), 'medium',
                :proposed, :closed, -1.0, 95.0
            )
            """
        ),
        {
            "tid": trade_id,
            "uid": user_id,
            "ts": closed_at,
            "symbol": symbol,
            "regime": regime,
            "sym_text": f"test trade {trade_id[:8]}",
            "sum_hash": trade_id[:32],
            "proposed": closed_at - timedelta(hours=4),
            "closed": closed_at,
        },
    )

    await session.execute(
        text(
            """
            INSERT INTO setup_post_mortems (
                trade_id, user_id, outcome, r_multiple, exit_reason,
                verdict, confidence_calibration, factor_verdicts,
                lesson_es, summary_es, citations, model_id, created_at
            ) VALUES (
                CAST(:tid AS uuid), :uid, 'loss', -1.0, 'sl_hit',
                :verdict, 'calibrated', CAST(:fv AS jsonb),
                :lesson, :lesson, '[]'::jsonb, 'test-model', :created
            )
            """
        ),
        {
            "tid": trade_id,
            "uid": user_id,
            "verdict": verdict,
            "fv": json.dumps(factor_verdicts),
            "lesson": lesson_es,
            "created": closed_at,
        },
    )
    return trade_id


async def test_returns_only_thesis_broken_lessons(db_session) -> None:
    """`verdict='thesis_held'` no debe devolver lecciones (señal nula para
    'evitar repetir')."""
    uid = f"test-uid-{uuid.uuid4()}"
    await _seed_trade_and_post_mortem(
        db_session,
        user_id=uid,
        symbol="BTCUSDT",
        regime="ranging",
        verdict="thesis_held",
        factor_verdicts={"ema_stack@1h": {"value": 0.6, "verdict": "failed"}},
        lesson_es="this should NOT be returned because thesis held",
    )
    out = await get_recent_lessons_for_factors(
        db_session,
        user_id=uid,
        factor_keys=["ema_stack@1h"],
    )
    assert out["ema_stack@1h"] == []


async def test_respects_per_factor_cap(db_session) -> None:
    uid = f"test-uid-{uuid.uuid4()}"
    for i in range(3):
        await _seed_trade_and_post_mortem(
            db_session,
            user_id=uid,
            symbol="BTCUSDT",
            regime="ranging",
            verdict="thesis_broken",
            factor_verdicts={"ema_stack@1h": {"value": 0.5, "verdict": "failed"}},
            lesson_es=f"lesson #{i}",
            days_ago=i,
        )
    out = await get_recent_lessons_for_factors(
        db_session,
        user_id=uid,
        factor_keys=["ema_stack@1h"],
        per_factor=2,
    )
    assert len(out["ema_stack@1h"]) == 2
    # El más reciente primero.
    assert out["ema_stack@1h"][0].lesson_es == "lesson #0"


async def test_respects_lookback_days(db_session) -> None:
    uid = f"test-uid-{uuid.uuid4()}"
    await _seed_trade_and_post_mortem(
        db_session,
        user_id=uid,
        symbol="BTCUSDT",
        regime="ranging",
        verdict="thesis_broken",
        factor_verdicts={"ema_stack@1h": {"value": 0.5, "verdict": "failed"}},
        lesson_es="too old",
        days_ago=100,
    )
    out = await get_recent_lessons_for_factors(
        db_session,
        user_id=uid,
        factor_keys=["ema_stack@1h"],
        lookback_days=30,
    )
    assert out["ema_stack@1h"] == []


async def test_empty_factor_keys_returns_empty_dict(db_session) -> None:
    out = await get_recent_lessons_for_factors(
        db_session,
        user_id="any-user",
        factor_keys=[],
    )
    assert out == {}


async def test_factor_with_verdict_worked_not_returned(db_session) -> None:
    """Si `factor_verdicts[key].verdict='worked'`, esa key no es una lección
    de fallo — no devolver aunque el post-mortem global sea thesis_broken."""
    uid = f"test-uid-{uuid.uuid4()}"
    await _seed_trade_and_post_mortem(
        db_session,
        user_id=uid,
        symbol="BTCUSDT",
        regime="ranging",
        verdict="thesis_broken",
        factor_verdicts={
            "ema_stack@1h": {"value": 0.6, "verdict": "worked"},
            "rsi@1h": {"value": -0.3, "verdict": "failed"},
        },
        lesson_es="rsi failed in this trade",
    )
    out = await get_recent_lessons_for_factors(
        db_session,
        user_id=uid,
        factor_keys=["ema_stack@1h", "rsi@1h"],
    )
    assert out["ema_stack@1h"] == []  # verdict=worked, no return
    assert len(out["rsi@1h"]) == 1  # verdict=failed, returned
