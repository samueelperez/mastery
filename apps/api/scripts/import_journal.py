"""Import a CSV of past trades into journal_trades, embedding the summary text.

Expected CSV columns (header required):
    ts,symbol,timeframe,side,entry_px,exit_px,size,r_multiple,setup_tag,regime,mistakes

Usage:
    cd apps/api && uv run python scripts/import_journal.py path/to/trades.csv
    # or with a sample fixture:
    uv run python scripts/import_journal.py tests/fixtures/sample_trades.csv

Behavior:
    - Embeds in batches of 16 (well within Voyage's batch limit).
    - Idempotent insert is NOT enforced — running twice will create duplicates.
      Wipe first if you want to re-import: TRUNCATE journal_trades.
"""

from __future__ import annotations

import asyncio
import csv
import sys
from datetime import datetime
from pathlib import Path

import structlog

from app.core.db import dispose_engine, init_engine, session_scope
from app.journal.embeddings import INPUT_TYPE_DOCUMENT, embed_batch
from app.journal.summary import build_summary_text, hash_summary
from app.storage.journal_repo import JournalTradeIn, insert_trade

log = structlog.get_logger(__name__)


def _parse_row(row: dict[str, str]) -> dict:
    def opt_float(s: str | None) -> float | None:
        if s is None or s.strip() == "":
            return None
        return float(s)

    return {
        "trade_ts": datetime.fromisoformat(row["ts"].replace("Z", "+00:00")),
        "symbol": row["symbol"].upper(),
        "timeframe": row["timeframe"],
        "side": row["side"],
        "entry_px": float(row["entry_px"]),
        "exit_px": opt_float(row.get("exit_px")),
        "size": float(row["size"]),
        "r_multiple": opt_float(row.get("r_multiple")),
        "setup_tag": row["setup_tag"],
        "regime": row["regime"],
        "mistakes": (row.get("mistakes") or "").strip() or None,
    }


async def _run(csv_path: Path) -> None:
    init_engine()
    log.info("import_journal.start", file=str(csv_path))

    with csv_path.open() as f:
        rows = [_parse_row(r) for r in csv.DictReader(f)]
    if not rows:
        log.warning("import_journal.empty")
        return

    summaries = [
        build_summary_text(
            {
                "setup_tag": r["setup_tag"],
                "regime": r["regime"],
                "side": r["side"],
                "symbol": r["symbol"],
                "timeframe": r["timeframe"],
                "r_multiple": r["r_multiple"],
                "mistakes": r["mistakes"],
            }
        )
        for r in rows
    ]

    # Embed in batches of 16 (Voyage limit is 128; 16 keeps memory bounded).
    BATCH = 16
    embeddings: list[list[float]] = []
    for i in range(0, len(summaries), BATCH):
        batch = summaries[i : i + BATCH]
        embeddings.extend(await embed_batch(batch, input_type=INPUT_TYPE_DOCUMENT))
        log.info("import_journal.embed_batch", offset=i, n=len(batch))

    inserted = 0
    async with session_scope() as session:
        for row, summary, emb in zip(rows, summaries, embeddings, strict=True):
            await insert_trade(
                session,
                JournalTradeIn(
                    trade_ts=row["trade_ts"],
                    symbol=row["symbol"],
                    timeframe=row["timeframe"],
                    mode="csv_import",
                    side=row["side"],
                    entry_px=row["entry_px"],
                    exit_px=row["exit_px"],
                    size=row["size"],
                    r_multiple=row["r_multiple"],
                    setup_tag=row["setup_tag"],
                    regime=row["regime"],
                    mistakes=row["mistakes"],
                    summary_text=summary,
                    summary_hash=hash_summary(summary),
                    embedding=emb,
                ),
            )
            inserted += 1

    await dispose_engine()
    log.info("import_journal.done", inserted=inserted)


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: uv run python scripts/import_journal.py <path/to/trades.csv>")
        raise SystemExit(2)
    csv_path = Path(sys.argv[1])
    if not csv_path.exists():
        print(f"file not found: {csv_path}")
        raise SystemExit(2)
    asyncio.run(_run(csv_path))


if __name__ == "__main__":
    main()
