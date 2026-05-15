# 06 — Telegram Integration for Ground Truth

<context>
During weeks 1-4 of paper trading, the operator validates each proposed setup against their TradingDifferent dashboard from the phone. This is the ground-truth feedback that powers M2 weight calibration. The integration adds 3 buttons to the existing setup-alert inline keyboard and a webhook handler that persists the verdict to `liquidation_agreement_log`.

After week 4 (when adaptive weights are computed and the system runs on calibrated data), the validation step is disabled by config flag. The code stays in place — it's the same flag that re-enables manual ground-truth collection if the system drifts in M3+.
</context>

<deliverables>
- Modifications to `apps/api/app/notifications/telegram.py`:
  - Extend `format_setup_alert()` to include magnet zone preview.
  - Extend `_inline_kb_for_setup()` to add 3 ground-truth buttons.
- Modifications to `apps/api/app/notifications/routes.py`:
  - Webhook handler accepts new callback_data prefixes (`gt:agree:<setup_id>`, `gt:close:<setup_id>`, `gt:disagree:<setup_id>`).
- New file `apps/api/app/liquidation/telegram_handlers.py` — persistence logic.
- New Settings flag `ground_truth_collection_enabled` (default `True` for M1; toggle to `False` at start of M2).
- Modifications to `apps/api/tests/notifications/test_telegram_format.py` — tests for the new keyboard rows.
- New file `apps/api/tests/liquidation/test_telegram_handlers.py` — handler logic.
</deliverables>

<setup_alert_extension>

`format_setup_alert(chat_id, setup_id, idea)` in `apps/api/app/notifications/telegram.py` currently produces a MarkdownV2 message with direction, symbol, TF, entry, SL, TPs, confidence, regime, summary.

Extend it to append a "Magnet zones" section IF the citation contract includes a `get_liquidation_heatmap` citation. Do NOT call the tool again here — read the snapshot from the citation's stored data (the setup row carries `factor_snapshot` jsonb which includes the citation outputs).

Append to the message:

```
🧲 *Magnet zones \(±5%\)*
  Below: $84\,000 \-\- 84\,200  \($180M, longs liq\)
  Above: $85\,400 \-\- 85\,600  \($95M, shorts liq\)
  Agreement: 0\.91 \(high\)

Validar contra TradingDifferent:
```

Note the MarkdownV2 escapes already applied. Helper `_escape_md` exists in the same file — use it for dollar amounts and prices.

If the setup has NO heatmap citation (rare — most directional setups should have one), skip the section entirely. Don't fail.
</setup_alert_extension>

<inline_keyboard_extension>

`_inline_kb_for_setup(setup_id)` currently returns:

```python
[
    [{"text": "✋ Approve", "callback_data": f"a:{setup_id}"},
     {"text": "🚫 Reject", "callback_data": f"r:{setup_id}"}],
    [{"text": "📊 Open chart", "url": chart_url}],
]
```

Extend to (when `ground_truth_collection_enabled` is True):

```python
[
    [{"text": "✅ TD agrees", "callback_data": f"gt:agree:{setup_id}"},
     {"text": "⚠️ TD close", "callback_data": f"gt:close:{setup_id}"},
     {"text": "❌ TD disagrees", "callback_data": f"gt:disagree:{setup_id}"}],
    [{"text": "✋ Approve", "callback_data": f"a:{setup_id}"},
     {"text": "🚫 Reject", "callback_data": f"r:{setup_id}"}],
    [{"text": "📊 Open chart", "url": chart_url}],
]
```

**Callback data size constraint**: Telegram limit is 64 bytes for `callback_data`. With `gt:disagree:<uuid>`, that's 14 + 36 = 50 bytes. Safe. Test the upper bound explicitly (existing test `test_inline_kb_for_setup_callback_size_under_64` already covers basic; extend to cover gt prefixes).

When the flag is False, fall back to the original 2-row keyboard (no `gt:*` buttons).
</inline_keyboard_extension>

<file_apps_api_app_liquidation_telegram_handlers_py>

```python
"""Telegram callback handlers for ground-truth collection.

Invoked from `notifications/routes.py::_telegram_webhook` when a callback_data
starting with `gt:` is received. Resolves the user from chat_id, finds the
setup, and persists a row to `liquidation_agreement_log`.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.liquidation.models import TDVerdict

LOG = logging.getLogger(__name__)


async def record_ground_truth(
    *,
    session_factory: async_sessionmaker,
    user_id: str,
    setup_id: str,
    verdict: TDVerdict,
) -> bool:
    """Persist a ground-truth verdict from the operator. Returns True on
    success. Idempotent: a second call for the same setup_id updates the
    existing row instead of duplicating.

    Args:
        session_factory: async session factory from AgentDeps or app state.
        user_id: scoped user.
        setup_id: UUID of the setup the verdict refers to.
        verdict: 'agree' | 'close' | 'disagree'. Never 'skipped' (that's
            inferred when the timeout fires without a click).

    Returns:
        True if a row was inserted/updated, False if the setup couldn't be
        found or the verdict was malformed.
    """
    if verdict not in ("agree", "close", "disagree"):
        LOG.warning("invalid_gt_verdict", extra={"verdict": verdict})
        return False

    async with session_factory() as session:
        # Resolve the setup's proposed magnet zone from journal_trades.
        # We assume the setup row carries `factor_snapshot` jsonb containing
        # citation snapshots, including the get_liquidation_heatmap one.
        row = await session.execute(
            text("""
                SELECT symbol, factor_snapshot
                FROM journal_trades
                WHERE id = :setup_id AND user_id = :user_id
            """),
            {"setup_id": setup_id, "user_id": user_id},
        )
        setup = row.first()
        if not setup:
            LOG.warning("gt_setup_not_found", extra={"setup_id": setup_id})
            return False

        fs = setup.factor_snapshot or {}
        liq = (fs.get("get_liquidation_heatmap") or {})
        if not liq:
            LOG.warning("gt_no_heatmap_citation", extra={"setup_id": setup_id})
            return False

        # Proposed zone: the nearest zone in the direction relevant to the
        # setup. For an agent that proposed correctly, this is the citation's
        # nearest_short_liq for a long setup, etc. We persist whatever the
        # citation referenced.
        proposed_price = (
            liq.get("nearest_short_liq_price")
            or liq.get("nearest_long_liq_price")
        )
        proposed_side = (
            "short_liq" if liq.get("nearest_short_liq_price")
            else "long_liq"
        )
        if proposed_price is None:
            LOG.warning("gt_no_proposed_zone", extra={"setup_id": setup_id})
            return False

        # Real provider prices for delta computation.
        source_a_price = liq.get("source_breakdown_a_price")
        source_b_price = liq.get("source_breakdown_b_price")
        timeframe = liq.get("timeframe") or "4h"

        delta_a = (
            abs(source_a_price - proposed_price) / proposed_price * 100
            if source_a_price else None
        )
        delta_b = (
            abs(source_b_price - proposed_price) / proposed_price * 100
            if source_b_price else None
        )

        # Idempotent: ON CONFLICT (setup_id) update (we don't have the unique
        # index by default; either add it in migration 025 or do a manual
        # check-then-update).
        await session.execute(
            text("""
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
            """),
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
            extra={"setup_id": setup_id, "verdict": verdict, "delta_a": delta_a, "delta_b": delta_b},
        )
        return True
```
</file_apps_api_app_liquidation_telegram_handlers_py>

<webhook_routing>

In `apps/api/app/notifications/routes.py::_telegram_webhook`, the callback dispatch currently handles `a:<setup>` and `r:<setup>`. Add a branch for `gt:`:

```python
data = callback_query.get("data", "")

if data.startswith("gt:"):
    # gt:<verdict>:<setup_id>
    _, verdict, setup_id = data.split(":", 2)
    user_id = await _resolve_user_from_chat(session_factory, chat_id)
    if not user_id:
        return  # silent — same pattern as a:/r:
    await record_ground_truth(
        session_factory=session_factory,
        user_id=user_id,
        setup_id=setup_id,
        verdict=verdict,
    )
    # Send a small ack so the button doesn't look stuck.
    await answer_callback_query(callback_query["id"], text=f"OK ({verdict})")
    return

if data.startswith("a:") or data.startswith("r:"):
    # existing logic unchanged
    ...
```

`record_ground_truth` is the function from `liquidation/telegram_handlers.py`. `answer_callback_query` is a helper for Telegram's `answerCallbackQuery` method — add it if it doesn't exist (it's a 5-line POST).

The webhook handler MUST still return 200 even on internal errors — Telegram retries otherwise. The existing pattern in `notifications.md` says: "Siempre responde {ok: true} aunque el handler interno lance — evita retry storm de Telegram ante un bug; el error se loguea con log.exception." Preserve it.
</webhook_routing>

<settings_addition>

In `apps/api/app/core/config.py::Settings`, add:

```python
class Settings(BaseSettings):
    # ... existing fields ...

    ground_truth_collection_enabled: bool = Field(
        default=True,
        description="If True, the setup alert keyboard includes 3 ground-truth "
                    "buttons for TradingDifferent validation. Disable at start of M2 "
                    "after weights have been calibrated.",
    )
```
</settings_addition>

<gotchas>
- MarkdownV2 escaping is brutal — `_escape_md` exists for a reason. Test any new template snippet with a price that contains a decimal point (e.g. `$84,200.50`). The `.` needs to be escaped.
- Telegram's `callback_data` is 64 bytes (not chars). UUIDs are 36 chars = 36 bytes. Prefixes total ≤14 bytes. Margin is fine. But if you ever switch UUIDs to base32, recompute.
- The webhook MUST verify `X-Telegram-Bot-Api-Secret-Token` with `secrets.compare_digest`. This is already in the existing code; don't accidentally bypass for `gt:` callbacks.
- Idempotency: if the operator double-taps `agree`, the same setup_id gets two rows. Add a UNIQUE constraint on `(setup_id, source_c_verdict)` in migration 025? **No — better to keep history**. The calibration job dedupes by `DISTINCT ON (setup_id) ORDER BY logged_at DESC`.
- `factor_snapshot` jsonb structure is set by `setups/repo.py::insert_setup_from_idea` from the validator. Make sure that function persists the heatmap citation's snapshot under key `get_liquidation_heatmap`. If it doesn't (it probably doesn't yet because this is new), extend `insert_setup_from_idea` in this PR.
- `answer_callback_query` returns within 200ms or Telegram considers the button stuck. Don't `await` long DB work before calling it; consider firing the persistence as a background task.
- `_resolve_user_from_chat` already exists in the notifications module (per `notifications.md`). Use it; don't reimplement.
- A user could theoretically tap `gt:agree:<setup_id>` for a setup that isn't theirs (if they had two accounts and confused chats). The existing `_resolve_user_from_chat` + the `user_id = :user_id` clause in the SELECT prevent cross-user writes.
- Don't add `gt:` buttons to setups that have no heatmap citation. The setup_alert template should branch: if no heatmap, render the original 2-row keyboard.
</gotchas>

<acceptance>
- [ ] `format_setup_alert` appends magnet zone section when heatmap citation exists.
- [ ] `format_setup_alert` omits the section when no heatmap citation (no failure).
- [ ] `_inline_kb_for_setup` produces 3 rows when `ground_truth_collection_enabled=True`.
- [ ] All callback_data strings under 64 bytes.
- [ ] Webhook dispatches `gt:` callbacks to `record_ground_truth`.
- [ ] `record_ground_truth` is idempotent (same setup, multiple calls → multiple rows, but no errors).
- [ ] `record_ground_truth` writes correct `delta_a_pct` / `delta_b_pct` from source breakdown.
- [ ] Setting `ground_truth_collection_enabled=False` reverts to original keyboard.
- [ ] Webhook returns 200 even if `record_ground_truth` raises (logged).
- [ ] Test cases: agree / close / disagree / unknown verdict / missing setup / missing heatmap citation.
</acceptance>
