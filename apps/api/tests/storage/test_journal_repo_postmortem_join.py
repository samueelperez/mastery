"""Tests para el LEFT JOIN setup_post_mortems en `journal_repo.hybrid_search`.

Verifica:
- `JournalSearchHit.post_mortem` poblado cuando el trade tiene post-mortem.
- `JournalSearchHit.post_mortem` = None cuando NO existe post-mortem.
- Los lists `failure_factors`/`success_factors` parsean correctamente
  desde JSONB.
- No rompe el shape pre-existente del hit (id, summary_text, rrf_score, etc).

Requiere DB con migraciones 011-015 aplicadas + pgvector + tsvector indexes.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text

from app.storage.journal_repo import hybrid_search

pytestmark = pytest.mark.asyncio


async def _seed_trade(
    session: Any,
    *,
    user_id: str,
    summary_text: str = "test setup trade ranging btcusdt",
    embedding: list[float] | None = None,
) -> str:
    """Crea un trade mínimo con embedding y tsvector populados."""
    trade_id = str(uuid.uuid4())
    if embedding is None:
        embedding = [0.1] * 1024
    emb_str = "[" + ",".join(f"{x:.6f}" for x in embedding) + "]"

    await session.execute(
        text(
            """
            INSERT INTO journal_trades (
                id, user_id, trade_ts, symbol, timeframe, mode, side,
                entry_px, size, setup_tag, regime, mistakes, news_24h,
                features, summary_text, summary_hash, embedding_version,
                embedding, status, source, stop_loss_px, targets,
                confidence, proposed_at, closed_at, r_multiple, exit_px
            ) VALUES (
                CAST(:tid AS uuid), :uid, now(), 'BTCUSDT', '1h',
                'manual_log', 'long', 100.0, 0.0, 'long_ranging_1h',
                'ranging', NULL, '{}'::jsonb, CAST('{}' AS jsonb),
                :sym, :h, 1, CAST(:emb AS vector),
                'closed', 'agent_proposal', 95.0, CAST('[]' AS jsonb),
                'medium', now() - interval '4h', now(), -1.0, 95.0
            )
            """
        ),
        {
            "tid": trade_id,
            "uid": user_id,
            "sym": summary_text,
            "h": trade_id[:32],
            "emb": emb_str,
        },
    )
    return trade_id


async def _seed_post_mortem_correct(
    session: Any,
    *,
    trade_id: str,
    user_id: str,
    verdict: str = "thesis_broken",
    lesson: str = "test lesson — exigir confirmación volume",
) -> None:
    """Usa solo las columnas reales del schema post-migración 015. Las listas
    failure_factors/success_factors NO son columnas propias — viven dentro
    de factor_verdicts JSONB en el insert real del dispatcher (`{factor_key:
    {verdict: 'failed'|'worked'|'neutral'}}`)."""
    await session.execute(
        text(
            """
            INSERT INTO setup_post_mortems (
                trade_id, user_id, outcome, r_multiple, exit_reason,
                verdict, confidence_calibration, factor_verdicts,
                lesson_es, summary_es, citations, model_id, created_at
            ) VALUES (
                CAST(:tid AS uuid), :uid, 'loss', -1.0, 'sl_hit',
                :verdict, 'calibrated',
                jsonb_build_object(
                    'ema_stack@1h', jsonb_build_object(
                        'value', 0.5, 'verdict', 'failed'
                    ),
                    'rsi@1h', jsonb_build_object(
                        'value', -0.3, 'verdict', 'failed'
                    )
                ),
                :lesson, :lesson, '[]'::jsonb, 'test-model', now()
            )
            """
        ),
        {
            "tid": trade_id,
            "uid": user_id,
            "verdict": verdict,
            "lesson": lesson,
        },
    )


async def test_hybrid_search_returns_post_mortem_when_present(
    db_session,
) -> None:
    uid = f"test-uid-{uuid.uuid4()}"
    trade_id = await _seed_trade(db_session, user_id=uid)
    await _seed_post_mortem_correct(
        db_session, trade_id=trade_id, user_id=uid,
    )

    hits = await hybrid_search(
        db_session,
        query_text="ranging btcusdt setup",
        query_embedding=[0.1] * 1024,
        k=5,
        user_id=uid,
    )
    assert len(hits) >= 1
    matching = next((h for h in hits if h.id == trade_id), None)
    assert matching is not None, "trade no encontrado en hits"
    assert matching.post_mortem is not None
    assert matching.post_mortem.verdict == "thesis_broken"
    assert "exigir confirmación volume" in matching.post_mortem.lesson_es


async def test_hybrid_search_post_mortem_none_when_absent(
    db_session,
) -> None:
    uid = f"test-uid-{uuid.uuid4()}"
    trade_id = await _seed_trade(db_session, user_id=uid)
    # NO seed post-mortem.

    hits = await hybrid_search(
        db_session,
        query_text="ranging btcusdt setup",
        query_embedding=[0.1] * 1024,
        k=5,
        user_id=uid,
    )
    matching = next((h for h in hits if h.id == trade_id), None)
    assert matching is not None
    assert matching.post_mortem is None
