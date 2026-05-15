"""Re-embed journal trades whose summary_text drifted from their stored hash.

Run periodically (manual / cron) to keep embeddings in sync with edited
post-mortems. Increments `embedding_version` on each refresh so you can audit
how many regenerations a row has received.
"""

from __future__ import annotations

import asyncio

import structlog

from app.core.db import dispose_engine, init_engine, session_scope
from app.journal.embeddings import INPUT_TYPE_DOCUMENT, embed_batch
from app.journal.repo import (
    list_all_for_embed_check,
    list_users_with_trades,
    update_summary_and_embedding,
)
from app.journal.summary import build_summary_text, hash_summary

log = structlog.get_logger(__name__)


async def _refresh_user(session: object, user_id: str) -> tuple[int, int]:
    """Refresh embeddings for one user. Returns (refreshed, skipped)."""
    rows = await list_all_for_embed_check(
        session,  # type: ignore[arg-type]
        user_id=user_id,
        batch_size=1000,
    )

    # Determine which rows need a refresh in Python (avoids pgcrypto dep).
    # Tuple is (id, new_summary, new_version, observed_old_hash) — the old
    # hash flows into update_summary_and_embedding's CAS so a concurrent
    # edit doesn't get clobbered by a stale embedding.
    stale: list[tuple[str, str, int, str]] = []
    for r in rows:
        new_summary = build_summary_text(
            {
                "setup_tag": r.setup_tag,
                "regime": r.regime,
                "side": r.side,
                "symbol": r.symbol,
                "timeframe": r.timeframe,
                "r_multiple": r.r_multiple,
                "mistakes": r.mistakes,
            }
        )
        new_hash = hash_summary(new_summary)
        if new_hash != r.summary_hash:
            stale.append((r.id, new_summary, r.embedding_version + 1, r.summary_hash))

    if not stale:
        return 0, 0

    BATCH = 16
    refreshed = 0
    skipped = 0
    for i in range(0, len(stale), BATCH):
        chunk = stale[i : i + BATCH]
        new_summaries = [s for (_, s, _, _) in chunk]
        embeddings = await embed_batch(new_summaries, input_type=INPUT_TYPE_DOCUMENT)
        for (trade_id, summary, ver, old_hash), emb in zip(
            chunk, embeddings, strict=True
        ):
            ok = await update_summary_and_embedding(
                session,  # type: ignore[arg-type]
                trade_id=trade_id,
                summary_text=summary,
                summary_hash=hash_summary(summary),
                embedding=emb,
                embedding_version=ver,
                expected_old_hash=old_hash,
            )
            if ok:
                refreshed += 1
            else:
                skipped += 1
                log.info("embed_backfill.cas_miss", trade_id=trade_id, user_id=user_id)
        log.info(
            "embed_backfill.batch",
            user_id=user_id,
            offset=i,
            refreshed=refreshed,
            skipped=skipped,
        )
    return refreshed, skipped


async def _run() -> None:
    init_engine()
    total_refreshed = 0
    async with session_scope() as session:
        users = await list_users_with_trades(session)
        for uid in users:
            r, _s = await _refresh_user(session, uid)
            total_refreshed += r

    await dispose_engine()
    log.info("embed_backfill.done", refreshed=total_refreshed)


if __name__ == "__main__":
    asyncio.run(_run())
