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
from app.broadcasting.pubsub import publish_json, subscribe
from app.config import get_settings
from app.data.binance_adapter import EXCHANGE_NAME
from app.db import session_scope
from app.indicators import IndicatorSpec, compute_panel
from app.ingestion.live_klines import WATCH_LIST

log = structlog.get_logger(__name__)


def alerts_channel(user_id: str) -> str:
    return f"alerts:user:{user_id}"


def _market_channel(exchange: str, symbol: str, timeframe: str) -> str:
    # Local copy of pubsub.market_channel — kept here so a future split of
    # ingestion vs alerts modules doesn't tangle imports.
    return f"mkt:{exchange}:{symbol.lower()}:k:{timeframe}"


async def _fetch_active_rules(
    session: AsyncSession, *, symbol: str, timeframe: str
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                """
                SELECT id::text, user_id, name, spec, cooldown_s, last_fired_at
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
    """Heuristic: the panel needs at least max(spec.length) + buffer rows.
    300 is plenty for any indicator we currently expose (longest default is
    EMA-200ish; ADX needs 2× length warmup)."""
    lengths = [s.length or 50 for s in specs]
    return max(300, max(lengths, default=50) * 3)


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


async def _record_hit(
    session: AsyncSession,
    *,
    rule: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
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
    return {
        "event_id": row["id"],
        "rule_id": rule["id"],
        "rule_name": rule["name"],
        "fired_at": row["fired_at"].isoformat(),
        "kind": "rule_match",
        "severity": "medium",
        "snapshot": snapshot,
    }


async def _evaluate_close(symbol: str, timeframe: str) -> None:
    """Single closed-candle pass — read rules, compute panel once, evaluate all."""
    async with session_scope() as session:
        rules = await _fetch_active_rules(session, symbol=symbol, timeframe=timeframe)
    if not rules:
        return

    rule_specs: list[RuleSpec] = []
    for r in rules:
        try:
            rule_specs.append(RuleSpec.model_validate(r["spec"]))
        except Exception as exc:
            log.warning(
                "alerts.skip_invalid_spec", rule_id=r["id"], error=str(exc)
            )
            rule_specs.append(None)  # type: ignore[arg-type]

    union = _union_specs([s.indicators for s in rule_specs if s is not None])
    lookback = _max_lookback(union)
    until = floor_to_timeframe(datetime.now(tz=UTC), timeframe)

    async with session_scope() as session:
        panel = await compute_panel(
            session,
            exchange=EXCHANGE_NAME,
            symbol=symbol,
            timeframe=timeframe,
            lookback=lookback,
            specs=union,
            until=until,
        )

    if panel.height == 0:
        return

    for rule, spec in zip(rules, rule_specs, strict=True):
        if spec is None:
            continue
        if not evaluate_rule(spec, panel):
            continue
        if _is_within_cooldown(rule):
            log.info(
                "alerts.cooldown_blocked",
                rule_id=rule["id"],
                last_fired_at=rule["last_fired_at"].isoformat() if rule["last_fired_at"] else None,
            )
            continue
        snapshot = build_snapshot(spec, panel)
        async with session_scope() as session:
            payload = await _record_hit(session, rule=rule, snapshot=snapshot)
        await publish_json(alerts_channel(rule["user_id"]), payload)
        log.info(
            "alerts.fired",
            rule_id=rule["id"],
            event_id=payload["event_id"],
            symbol=symbol,
            timeframe=timeframe,
        )


async def _market_loop(symbol: str, timeframe: str) -> None:
    channel = _market_channel(EXCHANGE_NAME, symbol, timeframe)
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
                    try:
                        ev = json.loads(payload)
                    except Exception:
                        log.warning("alerts.bias_payload_unparseable", payload=payload)
                        return
                    await _record_bias_promoted(ev)

                await conn.add_listener("bias_events_high", _on_notify)
                # Block until cancelled — Event.wait() with no setter parks
                # the task without spinning a sleep loop.
                await asyncio.Event().wait()
            finally:
                with contextlib.suppress(Exception):
                    await conn.close()
        except asyncio.CancelledError:
            log.info("alerts.bias_listener.cancelled")
            raise
        except Exception as exc:
            log.warning("alerts.bias_listener.error", error=str(exc))
            await asyncio.sleep(2.0)


async def _record_bias_promoted(bias_event: dict[str, Any]) -> None:
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
                {
                    "uid": bias_event.get("user_id", "me"),
                    "snap": json.dumps(bias_event),
                },
            )
        ).mappings().one()
    payload = {
        "event_id": row["id"],
        "rule_id": None,
        "rule_name": f"bias:{bias_event.get('kind', 'unknown')}",
        "fired_at": row["fired_at"].isoformat(),
        "kind": "bias_promoted",
        "severity": "high",
        "snapshot": bias_event,
    }
    await publish_json(alerts_channel(bias_event.get("user_id", "me")), payload)
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
                # 1m candles fire every 60s — too noisy for alerts. Skip.
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
