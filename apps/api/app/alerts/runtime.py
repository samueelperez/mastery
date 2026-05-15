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
from datetime import UTC, datetime
from typing import Any

import asyncpg
import orjson
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import floor_to_timeframe
from app.alerts.dsl import RuleSpec
from app.alerts.evaluator import build_snapshot, evaluate_rule
from app.alerts.panel_service import compute_panel_for_specs
from app.core.broadcasting.pubsub import market_channel, publish_json, subscribe
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.exchanges.binance_adapter import EXCHANGE_NAME
from app.market.ohlcv.ingestion_live import get_watch_list

log = structlog.get_logger(__name__)

# Scout dispatcher fire-and-forget tasks. Holding strong refs prevents GC
# from killing in-flight agent invocations. Cleared by done-callback.
_SCOUT_TASKS: set[asyncio.Task[Any]] = set()


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
                       updated_at, is_scout_trigger
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
    """Insert N alert_events + atomic claim of last_fired_at for each rule.

    Atomic claim (audit fix 2026-05): el UPDATE de `last_fired_at` se ejecuta
    como gate con WHERE condicional sobre cooldown — si dos workers ven la
    misma regla cooled-down simultáneamente, sólo uno consigue rowcount=1
    y mete el evento. El otro rebota con `cooldown_blocked_atomic`.

    Returns `(rule, payload)` pairs en orden de input.
    """
    out: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for rule, snapshot in hits:
        cooldown_s = float(rule.get("cooldown_s") or 0)
        # Atomic claim: UPDATE bumpea last_fired_at sólo si está fuera de
        # cooldown. rowcount=0 → otro worker se llevó el slot.
        claim = await session.execute(
            text(
                """
                UPDATE alert_rules
                SET last_fired_at = now(), updated_at = now()
                WHERE id = CAST(:rid AS uuid)
                  AND (
                    :cd <= 0
                    OR last_fired_at IS NULL
                    OR last_fired_at < now() - make_interval(secs => :cd)
                  )
                """
            ),
            {"rid": rule["id"], "cd": cooldown_s},
        )
        if (claim.rowcount or 0) == 0:  # type: ignore[attr-defined]
            log.info(
                "alerts.cooldown_blocked_atomic",
                rule_id=rule["id"],
                user_id=rule["user_id"],
            )
            continue

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
    persist hits in one transaction.

    Las sesiones se abren por fases (audit fix 2026-05): retener una única
    session_scope durante el `compute_panel_for_specs` agotaba el pool bajo
    carga porque Timescale I/O bloqueaba el slot durante toda la query.
    """
    # Phase 1: fetch + compile active rules.
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

    # Phase 2: compute panel in a fresh session (Timescale heavy I/O).
    until = floor_to_timeframe(datetime.now(tz=UTC), timeframe)
    async with session_scope() as session:
        panel = await compute_panel_for_specs(
            session,
            symbol=symbol,
            timeframe=timeframe,
            specs=[s for _, s in compiled],
            until=until,
        )

    if panel.height == 0:
        return

    # Split hits: scout-triggered → dispatch to agent (C.1); else → alert_event.
    scout_hits: list[tuple[dict[str, Any], dict[str, Any]]] = []
    alert_hits: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for rule, spec in compiled:
        if not evaluate_rule(spec, panel):
            continue
        if _is_within_cooldown(rule):
            log.info("alerts.cooldown_blocked", rule_id=rule["id"])
            continue
        snapshot = build_snapshot(spec, panel)
        if rule.get("is_scout_trigger"):
            scout_hits.append((rule, snapshot))
        else:
            alert_hits.append((rule, snapshot))

    # Regular alerts path — unchanged.
    if alert_hits:
        async with session_scope() as session:
            recorded = await _record_hits_batch(session, hits=alert_hits)
        for rule, payload in recorded:
            await publish_json(alerts_channel(rule["user_id"]), payload)
            log.info(
                "alerts.fired",
                rule_id=payload["rule_id"],
                event_id=payload["event_id"],
                symbol=symbol,
                timeframe=timeframe,
            )

    # Scout path (C.1) — fire-and-forget dispatcher per match. Still bumps
    # `last_fired_at` so the rule's own cooldown applies symmetrically.
    if scout_hits:
        # Lazy import to avoid the agent module being imported by the alerts
        # runtime tests (and to keep the lifespan of the agent build out of
        # the alert path when the scout feature isn't in use).
        from app.setups.scout_dispatcher import dispatch_scout_match

        async with session_scope() as session:
            for rule, _ in scout_hits:
                await session.execute(
                    text(
                        "UPDATE alert_rules SET last_fired_at = now(), "
                        "updated_at = now() WHERE id = CAST(:rid AS uuid)"
                    ),
                    {"rid": rule["id"]},
                )
        now_utc = datetime.now(tz=UTC)
        for rule, snapshot in scout_hits:
            task = asyncio.create_task(
                dispatch_scout_match(
                    user_id=rule["user_id"],
                    rule_id=rule["id"],
                    rule_name=rule["name"],
                    symbol=symbol,
                    timeframe=timeframe,
                    snapshot=snapshot,
                    fired_at=now_utc,
                ),
                name=f"scout:{rule['id']}",
            )
            _SCOUT_TASKS.add(task)
            task.add_done_callback(_SCOUT_TASKS.discard)


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


# Advisory-lock id arbitrario (constante de proceso). Cualquier réplica que
# corra `_bias_listener_loop` intenta adquirir este lock; sólo una lo
# consigue y los demás reintentan. Single-elected listener evita la
# duplicación N× de `alert_events` cuando hay >1 worker (audit fix 2026-05).
_BIAS_LISTENER_LOCK_ID = 0x42_42_5B_15  # stable arbitrary int


async def _bias_listener_loop() -> None:
    """LISTEN bias_events_high — promote high-severity bias to an alert_event.

    Single-elected vía `pg_try_advisory_lock`: si la API corre con N réplicas
    o N workers, solo la primera consigue el lock y se suscribe; las demás
    reintentan cada 10s. Sin esto, cada réplica recibe la NOTIFY y promueve
    el mismo bias N veces → eventos duplicados (audit fix 2026-05).
    """
    settings = get_settings()
    # asyncpg expects 'postgresql://' not 'postgresql+asyncpg://'.
    raw_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    log.info("alerts.bias_listener.start")
    while True:
        try:
            conn = await asyncpg.connect(raw_url)
            try:
                got_lock = await conn.fetchval(
                    "SELECT pg_try_advisory_lock($1)", _BIAS_LISTENER_LOCK_ID
                )
                if not got_lock:
                    log.info("alerts.bias_listener.standby")
                    await conn.close()
                    await asyncio.sleep(10.0)
                    continue

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
                log.info("alerts.bias_listener.elected")
                # Park task; teardown on cancellation triggers the finally block.
                await asyncio.Event().wait()
            finally:
                with contextlib.suppress(Exception):
                    await conn.remove_listener("bias_events_high", _on_notify)
                with contextlib.suppress(Exception):
                    # Liberar el advisory lock libera al siguiente standby.
                    await conn.execute(
                        "SELECT pg_advisory_unlock($1)", _BIAS_LISTENER_LOCK_ID
                    )
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
    from pg_notify; we only parse what we need for the log/key fields.

    Validamos `user_id` explícito antes de aceptar: si la payload del trigger
    `notify_bias_high` cambia o un row corrupto se cuela, evitamos enviar el
    bias al user legacy `'me'` por accidente (audit fix 2026-05)."""
    try:
        bias_event = json.loads(bias_payload_json)
    except Exception:
        log.warning("alerts.bias_payload_unparseable", payload=bias_payload_json)
        return
    if not isinstance(bias_event, dict):
        log.warning("alerts.bias_payload_not_dict", payload=bias_payload_json[:200])
        return
    user_id = bias_event.get("user_id")
    if not user_id or not isinstance(user_id, str):
        log.warning(
            "alerts.bias_payload_missing_user",
            payload_keys=sorted(bias_event.keys()),
        )
        return
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
        for symbol, tf in get_watch_list():
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
        # Cancel + await market loops + bias listener.
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self._tasks.clear()

        # Drain in-flight scout dispatch tasks (audit fix 2026-05). Antes
        # quedaban como "Task was destroyed but it is pending!" si shutdown
        # ocurría mientras un scout estaba mid-flight → pérdida silenciosa
        # de proposals durante deploys.
        pending_scouts = list(_SCOUT_TASKS)
        if pending_scouts:
            log.info(
                "alerts.runtime.draining_scout_tasks",
                n_pending=len(pending_scouts),
            )
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending_scouts, return_exceptions=True),
                    timeout=10.0,
                )
            except TimeoutError:
                # Después del timeout cancelamos los restantes.
                for t in pending_scouts:
                    if not t.done():
                        t.cancel()
                log.warning(
                    "alerts.runtime.scout_drain_timeout",
                    n_cancelled=sum(1 for t in pending_scouts if not t.done()),
                )
        log.info("alerts.runtime.stop")
