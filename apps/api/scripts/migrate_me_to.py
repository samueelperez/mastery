"""One-shot data migration after first BetterAuth signup.

Existing rows from F0–F3 use `user_id='me'` as the legacy single-user marker.
After F3.5.A you'll have one real user in the `"user"` table; this script moves
all 'me' rows to that user's id in a single transaction. Idempotent — running
twice updates 0 rows the second time.

Usage:
    uv run python scripts/migrate_me_to.py <user_id>

To find your user_id after signup:
    docker exec trading-postgres psql -U trading -d trading -c 'SELECT id, email FROM "user";'
"""

from __future__ import annotations

import argparse
import asyncio

import structlog
from sqlalchemy import text

from app.core.db import dispose_engine, init_engine, session_scope

log = structlog.get_logger(__name__)


_TABLES = ("journal_trades", "bias_events", "alert_rules", "alert_events")


async def migrate(new_user_id: str) -> dict[str, int]:
    init_engine()
    counts: dict[str, int] = {}
    async with session_scope() as session:
        for table in _TABLES:
            result = await session.execute(
                text(f"UPDATE {table} SET user_id = :new WHERE user_id = 'me'"),
                {"new": new_user_id},
            )
            counts[table] = int(getattr(result, "rowcount", 0) or 0)
    await dispose_engine()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate user_id='me' rows to a real user.")
    parser.add_argument("user_id", help="The new user's id (from the user table).")
    args = parser.parse_args()
    counts = asyncio.run(migrate(args.user_id))
    total = sum(counts.values())
    log.info("migrate_me_to.done", target=args.user_id, **counts)
    print(f"Migrated {total} rows total: {counts}")


if __name__ == "__main__":
    main()
