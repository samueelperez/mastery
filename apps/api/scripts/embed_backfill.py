"""Re-embed journal trades whose summary_text drifted from their stored hash.

Run periodically (manual / cron) to keep embeddings in sync with edited
post-mortems. Increments `embedding_version` on each refresh so you can audit
how many regenerations a row has received.
"""

from __future__ import annotations

import asyncio

import structlog

from app.db import dispose_engine, init_engine, session_scope
from app.journal.embeddings import INPUT_TYPE_DOCUMENT, embed_batch
from app.journal.summary import build_summary_text, hash_summary
from app.storage.journal_repo import (
    list_all_for_embed_check,
    update_summary_and_embedding,
)

log = structlog.get_logger(__name__)


async def _run() -> None:
    init_engine()
    refreshed = 0
    async with session_scope() as session:
        rows = await list_all_for_embed_check(session, batch_size=1000)

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
                stale.append(
                    (r.id, new_summary, r.embedding_version + 1, r.summary_hash)
                )

        if not stale:
            log.info("embed_backfill.no_drift")
            return

        BATCH = 16
        skipped = 0
        for i in range(0, len(stale), BATCH):
            chunk = stale[i : i + BATCH]
            new_summaries = [s for (_, s, _, _) in chunk]
            embeddings = await embed_batch(new_summaries, input_type=INPUT_TYPE_DOCUMENT)
            for (trade_id, summary, ver, old_hash), emb in zip(
                chunk, embeddings, strict=True
            ):
                ok = await update_summary_and_embedding(
                    session,
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
                    log.info("embed_backfill.cas_miss", trade_id=trade_id)
            log.info(
                "embed_backfill.batch",
                offset=i,
                refreshed=refreshed,
                skipped=skipped,
            )

    await dispose_engine()
    log.info("embed_backfill.done", refreshed=refreshed)


if __name__ == "__main__":
    asyncio.run(_run())
