"""Live alert engine — runs as one asyncio task per (symbol, timeframe) pair
plus a single Postgres LISTEN task for bias auto-promotion.

Subscribes to the same Valkey channels the live ingestor publishes
(`mkt:{exchange}:{symbol}:k:{timeframe}`), filters for `is_closed=True`,
fetches the active rules matching that (symbol, timeframe), evaluates them in
one shared `compute_panel` pass, and on a hit:

  - Inserts an `alert_events` row (kind='rule_match' or 'bias_promoted').
  - Updates `alert_rules.last_fired_at` so the cooldown gate works.
  - Publishes the event to Valkey channel `alerts:user:{user_id}` so the WS
    `/ws/alerts` endpoint can fan out to the browser.

Bias promotion uses Postgres LISTEN/NOTIFY (trigger from migration 003) so
there's no polling — when `bias_events.severity='high'` is inserted, the
runtime gets the notification and promotes it to an alert_event in <100ms.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

import asyncpg
import orjson
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools._time import floor_to_timeframe
from app.alerts.dsl import RuleSpec
from app.alerts.evaluator import build_snapshot, evaluate_rule
from app.broadcasting.pubsub import market_channel, publish_json, subscribe
from app.config import get_settings
from app.data.binance_adapter import EXCHANGE_NAME
from app.db import session_scope
from app.indicators import IndicatorSpec, compute_panel
from app.ingestion.live_klines import WATCH_LIST

log = structlog.get_logger(__name__)


def alerts_channel(user_id: str) -> str:
    return f"alerts:user:{user_id}"


def _build_event_payload(
    *,
    event_id: int,
    rule_id: str | None,
    rule_name: str,
    fired_at: datetime,
    kind: str,
    severity: str,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Single source of truth for the WS-bound shape (also matches AlertEventPayload in lib/ws.ts)."""
    return {
        "event_id": event_id,
        "rule_id": rule_id,
        "rule_name": rule_name,
        "fired_at": fired_at.isoformat(),
        "kind": kind,
        "severity": severity,
        "snapshot": snapshot,
    }


async def _fetch_active_rules(
    session: AsyncSession, *, symbol: str, timeframe: str
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                """
                SELECT id::text, user_id, name, spec, cooldown_s, last_fired_at,
                       updated_at
                FROM alert_rules
                WHERE enabled = true
                  AND spec->>'symbol' = :sym
                  AND spec->>'timeframe' = :tf
                """
            ),
            {"sym": symbol.upper(), "tf": timeframe},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def _max_lookback(specs: Iterable[IndicatorSpec]) -> int:
    """The panel needs max(spec.length) × 3 rows to warm up indicators (Wilder
    smoothing on RSI/ATR/ADX needs ~2× length, plus headroom for cross_*
    operators reading the previous bar). Floor at 60 so a 1-rule RSI(14) tick
    fetches ~60 candles, not 300."""
    lengths = [s.length or 50 for s in specs]
    return max(60, max(lengths, default=50) * 3)


def _union_specs(specs_lists: list[list[IndicatorSpec]]) -> list[IndicatorSpec]:
    """Deduplicate IndicatorSpec across rules (so we compute each indicator
    once even if 5 rules want RSI(14))."""
    seen: dict[tuple[str, int | None, str], IndicatorSpec] = {}
    for specs in specs_lists:
        for s in specs:
            seen.setdefault((s.name, s.length, s.source), s)
    return list(seen.values())


def _is_within_cooldown(rule: dict[str, Any]) -> bool:
    last = rule.get("last_fired_at")
    if last is None:
        return False
    cooldown = float(rule.get("cooldown_s") or 0)
    if cooldown <= 0:
        return False
    age = (datetime.now(tz=UTC) - last).total_seconds()
    return bool(age < cooldown)


# Module-level cache of compiled RuleSpec keyed by (rule_id, updated_at). Specs
# are immutable while a rule is enabled, so the validator only runs when a rule
# is created or PATCHed. Cleared lazily — entries with stale updated_at are
# overwritten on the next tick.
_SPEC_CACHE: dict[str, tuple[datetime, RuleSpec]] = {}


def _compile_spec(rule: dict[str, Any]) -> RuleSpec | None:
    rule_id = rule["id"]
    updated_at = rule["updated_at"]
    cached = _SPEC_CACHE.get(rule_id)
    if cached is not None and cached[0] == updated_at:
        return cached[1]
    try:
        spec = RuleSpec.model_validate(rule["spec"])
    except Exception as exc:
        log.warning("alerts.skip_invalid_spec", rule_id=rule_id, error=str(exc))
        return None
    _SPEC_CACHE[rule_id] = (updated_at, spec)
    return spec


async def _record_hits_batch(
    session: AsyncSession,
    *,
    hits: list[tuple[dict[str, Any], dict[str, Any]]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Insert N alert_events + bump last_fired_at for N rules in one session.

    Returns `(rule, payload)` pairs in input order — the caller needs the rule
    to know which user_id to publish to.
    """
    out: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for rule, snapshot in hits:
        row = (
            await session.execute(
                text(
                    """
                    INSERT INTO alert_events (user_id, rule_id, kind, severity, snapshot)
                    VALUES (:uid, CAST(:rid AS uuid), 'rule_match', 'medium', CAST(:snap AS jsonb))
                    RETURNING id, fired_at
                    """
                ),
                {
                    "uid": rule["user_id"],
                    "rid": rule["id"],
                    "snap": json.dumps(snapshot),
                },
            )
        ).mappings().one()
        await session.execute(
            text(
                """
                UPDATE alert_rules SET last_fired_at = now(), updated_at = now()
                WHERE id = CAST(:rid AS uuid)
                """
            ),
            {"rid": rule["id"]},
        )
        out.append(
            (
                rule,
                _build_event_payload(
                    event_id=row["id"],
                    rule_id=rule["id"],
                    rule_name=rule["name"],
                    fired_at=row["fired_at"],
                    kind="rule_match",
                    severity="medium",
                    snapshot=snapshot,
                ),
            )
        )
    return out


async def _evaluate_close(symbol: str, timeframe: str) -> None:
    """Single closed-candle pass — read rules, compute panel once, evaluate all,
    persist hits in one transaction."""
    async with session_scope() as session:
        rules = await _fetch_active_rules(session, symbol=symbol, timeframe=timeframe)
        if not rules:
            return
        compiled: list[tuple[dict[str, Any], RuleSpec]] = []
        for r in rules:
            spec = _compile_spec(r)
            if spec is not None:
                compiled.append((r, spec))
        if not compiled:
            return

        union = _union_specs([s.indicators for _, s in compiled])
        until = floor_to_timeframe(datetime.now(tz=UTC), timeframe)
        panel = await compute_panel(
            session,
            exchange=EXCHANGE_NAME,
            symbol=symbol,
            timeframe=timeframe,
            lookback=_max_lookback(union),
            specs=union,
            until=until,
        )

    if panel.height == 0:
        return

    hits: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for rule, spec in compiled:
        if not evaluate_rule(spec, panel):
            continue
        if _is_within_cooldown(rule):
            log.info("alerts.cooldown_blocked", rule_id=rule["id"])
            continue
        hits.append((rule, build_snapshot(spec, panel)))

    if not hits:
        return

    async with session_scope() as session:
        recorded = await _record_hits_batch(session, hits=hits)

    for rule, payload in recorded:
        await publish_json(alerts_channel(rule["user_id"]), payload)
        log.info(
            "alerts.fired",
            rule_id=payload["rule_id"],
            event_id=payload["event_id"],
            symbol=symbol,
            timeframe=timeframe,
        )


async def _market_loop(symbol: str, timeframe: str) -> None:
    channel = market_channel(exchange=EXCHANGE_NAME, symbol=symbol, timeframe=timeframe)
    log.info("alerts.market_loop.start", channel=channel)
    while True:
        try:
            async with subscribe(channel) as pubsub:
                while True:
                    msg = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=30.0
                    )
                    if msg is None or msg.get("type") != "message":
                        continue
                    data = orjson.loads(msg["data"])
                    if not data.get("is_closed"):
                        continue
                    try:
                        await _evaluate_close(symbol, timeframe)
                    except Exception as exc:
                        log.exception(
                            "alerts.evaluate_close.error",
                            symbol=symbol,
                            timeframe=timeframe,
                            error=str(exc),
                        )
        except asyncio.CancelledError:
            log.info("alerts.market_loop.cancelled", channel=channel)
            raise
        except Exception as exc:
            log.warning(
                "alerts.market_loop.error", channel=channel, error=str(exc)
            )
            await asyncio.sleep(2.0)


async def _bias_listener_loop() -> None:
    """LISTEN bias_events_high — promote high-severity bias to an alert_event."""
    settings = get_settings()
    # asyncpg expects 'postgresql://' not 'postgresql+asyncpg://'.
    raw_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    log.info("alerts.bias_listener.start")
    while True:
        try:
            conn = await asyncpg.connect(raw_url)
            try:
                async def _on_notify(
                    _conn: asyncpg.Connection,
                    _pid: int,
                    _channel: str,
                    payload: str,
                ) -> None:
                    # Pass the raw JSON string straight through — the INSERT
                    # casts to jsonb without a Python round-trip.
                    await _record_bias_promoted(payload)

                await conn.add_listener("bias_events_high", _on_notify)
                # Park task; teardown on cancellation triggers the finally block.
                await asyncio.Event().wait()
            finally:
                with contextlib.suppress(Exception):
                    await conn.remove_listener("bias_events_high", _on_notify)
                with contextlib.suppress(Exception):
                    await conn.close()
        except asyncio.CancelledError:
            log.info("alerts.bias_listener.cancelled")
            raise
        except Exception as exc:
            log.warning("alerts.bias_listener.error", error=str(exc))
            await asyncio.sleep(2.0)


async def _record_bias_promoted(bias_payload_json: str) -> None:
    """Insert + publish in one shot. `bias_payload_json` is the raw JSON string
    from pg_notify; we only parse what we need for the log/key fields."""
    try:
        bias_event = json.loads(bias_payload_json)
    except Exception:
        log.warning("alerts.bias_payload_unparseable", payload=bias_payload_json)
        return
    user_id = bias_event.get("user_id", "me")
    async with session_scope() as session:
        row = (
            await session.execute(
                text(
                    """
                    INSERT INTO alert_events (user_id, rule_id, kind, severity, snapshot)
                    VALUES (:uid, NULL, 'bias_promoted', 'high', CAST(:snap AS jsonb))
                    RETURNING id, fired_at
                    """
                ),
                {"uid": user_id, "snap": bias_payload_json},
            )
        ).mappings().one()
    payload = _build_event_payload(
        event_id=row["id"],
        rule_id=None,
        rule_name=f"bias:{bias_event.get('kind', 'unknown')}",
        fired_at=row["fired_at"],
        kind="bias_promoted",
        severity="high",
        snapshot=bias_event,
    )
    await publish_json(alerts_channel(user_id), payload)
    log.info(
        "alerts.bias_promoted",
        bias_kind=bias_event.get("kind"),
        event_id=row["id"],
    )


class AlertsRuntime:
    """Lifecycle owner. Mirror of `LiveIngestion` — start/stop from FastAPI lifespan."""

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        if self._tasks:
            return
        for symbol, tf in WATCH_LIST:
            if tf == "1m":
                # 1m candles fire every 60s — too noisy for alerts.
                continue
            t = asyncio.create_task(
                _market_loop(symbol, tf), name=f"alerts:{symbol}:{tf}"
            )
            self._tasks.append(t)
        bias_task = asyncio.create_task(
            _bias_listener_loop(), name="alerts:bias_listener"
        )
        self._tasks.append(bias_task)
        log.info("alerts.runtime.start", n_market_loops=len(self._tasks) - 1)

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self._tasks.clear()
        log.info("alerts.runtime.stop")
