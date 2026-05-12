"""Watcher de setups: aplica transitions automáticas al cierre de cada candle.

Un asyncio task por (symbol, timeframe) suscrito al mismo canal Valkey que el
ingestor (`mkt:{exchange}:{symbol}:k:{timeframe}`). En cada vela cerrada:

  1. Lee setups con status ∈ (pending, active) para ese (symbol, tf).
  2. Para cada setup:
     - **PRE-ENTRY** (`pending`):
       a) Wall-clock expiry: si `expires_at <= candle_ts`, cancel con
          event=`invalidated` (cheapest check, no panel needed).
       b) Invalidation conditions: si alguna RuleSpec del setup hace match
          en su (symbol, tf), cancel con event=`invalidated`. La evaluación
          puede usar un (symbol, tf) DISTINTO al del propio setup (por
          ejemplo, setup 1h con invalidación 4h) → se computa el panel del
          TF de la condición, no del setup.
       c) Si nada invalidó, evalúa `_entry_hit` sobre la vela actual.
     - **POST-ENTRY** (`active`):
       - closed (sl_hit, r=-1) si la vela toca el SL.
       - marca TPs hit como hit_at; si TODOS hechos, cierra como tp_hit.
  3. Edge case: si una misma vela toca SL y TP, prevalece SL.

Además: una task separada (`_expiry_sweep_loop`) corre cada 60s para cubrir
setups cuyo `expires_at` cae ENTRE cierres de candle en TFs lentos (4h, 1d).

Patrón calcado de `app.alerts.runtime` — mismo lifecycle (start/stop desde
FastAPI lifespan), mismo subscribe/timeout/reconnect.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import Any

import orjson
import polars as pl
import structlog
from sqlalchemy import text

from app.agent.models import TriggerKind
from app.alerts.dsl import RuleSpec
from app.alerts.evaluator import build_snapshot, evaluate_rule
from app.alerts.panel_service import compute_panel_for_specs
from app.core.broadcasting.pubsub import market_channel, subscribe
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.exchanges.binance_adapter import EXCHANGE_NAME
from app.ingestion.live_klines import get_watch_list
from app.runtime.post_mortem_dispatcher import maybe_run_post_mortem
from app.runtime.review_dispatcher import maybe_run_review
from app.runtime.risk_manager import (
    BreakevenAction,
    TimeStopAction,
    TrailingAction,
    apply_risk_action_to_db,
    compute_risk_actions,
    fetch_atr_for_trailing,
    max_hold_for_tf,
)
from app.storage.review_repo import list_active_setups_due_for_time_review
from app.storage.setup_repo import (
    OpenSetupRow,
    has_approval_event,
    list_open_setups,
    list_pending_with_expiry,
    transition_status,
    transition_to_invalidated,
    update_targets_hits,
)

log = structlog.get_logger(__name__)


# Timeframes que el watcher escucha. 1m queda fuera (demasiado ruido para
# F1; los setups del agente se emiten en 15m/1h/4h/1d).
_WATCHED_TIMEFRAMES = ("15m", "1h", "4h", "1d")


# Reviews task set — fire-and-forget tasks que el dispatcher ejecuta en
# background. Manteniendo referencias evita GC prematuro de la task.
# Limpia automáticamente con add_done_callback cuando termina cada review.
_REVIEW_TASKS: set[asyncio.Task[Any]] = set()


def _fire_review(
    *,
    setup: OpenSetupRow,
    trigger_kind: TriggerKind,
    trigger_payload: dict[str, Any],
    current_price: float,
    candle_ts: datetime,
) -> None:
    """Spawn fire-and-forget review task. Errores quedan dentro del
    dispatcher; aquí solo aseguramos que la task no sea GC'd."""
    task = asyncio.create_task(
        maybe_run_review(
            setup=setup,
            trigger_kind=trigger_kind,
            trigger_payload=trigger_payload,
            current_price=current_price,
            candle_ts=candle_ts,
        ),
        name=f"review:{setup.id}:{trigger_kind}",
    )
    _REVIEW_TASKS.add(task)
    task.add_done_callback(_REVIEW_TASKS.discard)


# F5.5: post-mortem fire-and-forget (mismo patrón que _fire_review). Set
# separado para que el lifecycle de cada subsistema sea independiente.
_POST_MORTEM_TASKS: set[asyncio.Task[Any]] = set()


def _fire_post_mortem(
    *,
    trade_id: str,
    user_id: str,
    trigger_kind: str,
    candle_ts: datetime,
) -> None:
    """Spawn fire-and-forget post-mortem task. El dispatcher respeta el
    feature flag y la idempotencia (UNIQUE trade_id en setup_post_mortems)."""
    task = asyncio.create_task(
        maybe_run_post_mortem(
            trade_id=trade_id,
            user_id=user_id,
            trigger_kind=trigger_kind,
            candle_ts=candle_ts,
        ),
        name=f"post_mortem:{trade_id}:{trigger_kind}",
    )
    _POST_MORTEM_TASKS.add(task)
    task.add_done_callback(_POST_MORTEM_TASKS.discard)


# ---------------------------------------------------------------------------
# Trigger logic
# ---------------------------------------------------------------------------


def _entry_hit(side: str, entry: float, high: float, low: float) -> bool:
    if side == "long":
        # Pullback al entry: el precio toca/cruza desde arriba.
        return low <= entry <= high
    if side == "short":
        return low <= entry <= high
    return False


def _sl_hit(side: str, sl: float, high: float, low: float) -> bool:
    if side == "long":
        return low <= sl
    if side == "short":
        return high >= sl
    return False


def _tp_hit(side: str, tp_price: float, high: float, low: float) -> bool:
    if side == "long":
        return high >= tp_price
    if side == "short":
        return low <= tp_price
    return False


def _r_multiple(side: str, entry: float, sl: float, exit_px: float) -> float:
    risk = abs(entry - sl)
    if risk == 0:
        return 0.0
    if side == "long":
        return (exit_px - entry) / risk
    return (entry - exit_px) / risk


# ---------------------------------------------------------------------------
# Per-setup evaluation
# ---------------------------------------------------------------------------


def _parse_conditions(setup: OpenSetupRow) -> list[RuleSpec]:
    """Re-validate the persisted JSONB into typed RuleSpecs. Bad shapes are
    silently dropped with a log — they came in via the agent validator so
    this is defensive, not a real failure mode."""
    out: list[RuleSpec] = []
    for raw in setup.invalidation_conditions:
        spec_raw = raw.get("spec") if isinstance(raw, dict) else None
        if not isinstance(spec_raw, dict):
            continue
        try:
            out.append(RuleSpec.model_validate(spec_raw))
        except Exception as exc:
            log.warning(
                "setup.invalid_condition_spec",
                setup_id=setup.id,
                error=str(exc),
            )
    return out


async def _check_expiry_and_invalidate(
    setup: OpenSetupRow, *, now_ts: datetime
) -> bool:
    """Wall-clock expiry check. Returns True if the setup was invalidated."""
    if setup.expires_at is None:
        return False
    if setup.expires_at > now_ts:
        return False
    async with session_scope() as session:
        ok = await transition_to_invalidated(
            session,
            trade_id=setup.id,
            candle_ts=now_ts,
            payload={
                "reason": "expires_at",
                "expires_at": setup.expires_at.isoformat(),
                "now": now_ts.isoformat(),
            },
        )
    if ok:
        log.info(
            "setup.invalidated_expiry",
            setup_id=setup.id,
            symbol=setup.symbol,
            expires_at=setup.expires_at.isoformat(),
        )
    return ok


async def _check_conditions_and_invalidate(
    setup: OpenSetupRow,
    *,
    candle_ts: datetime,
    panel: pl.DataFrame,
    condition_index: int,
    spec: RuleSpec,
) -> bool:
    """Evaluate ONE invalidation condition's RuleSpec against an already-
    computed panel. Returns True if the setup transitioned to cancelled."""
    if not evaluate_rule(spec, panel):
        return False
    async with session_scope() as session:
        ok = await transition_to_invalidated(
            session,
            trade_id=setup.id,
            candle_ts=candle_ts,
            payload={
                "reason": "condition",
                "matched_condition_index": condition_index,
                "snapshot": build_snapshot(spec, panel),
            },
        )
    if ok:
        log.info(
            "setup.invalidated_condition",
            setup_id=setup.id,
            symbol=setup.symbol,
            timeframe=setup.timeframe,
            condition_index=condition_index,
            spec_symbol=spec.symbol,
            spec_tf=spec.timeframe,
        )
    return ok


async def _apply_risk_manager(
    setup: OpenSetupRow, *, close: float, candle_ts: datetime
) -> OpenSetupRow:
    """B.1 RiskManager pre-step: evalúa reglas determinísticas (BE move,
    trailing, time stop) ANTES del SL/TP check normal. Devuelve el setup
    con `stop_loss_px` y `risk_state` actualizados si alguna acción se aplicó,
    para que el SL check posterior use el nuevo nivel.

    Si el feature flag está off, no-op.
    Si una `TimeStopAction` se aplica, el setup queda `status='closed'` —
    el caller debe detectarlo y NO seguir con SL/TP.
    """
    if not get_settings().risk_manager_enabled:
        return setup
    if setup.status != "active":
        return setup

    settings = get_settings()
    max_hold = max_hold_for_tf(
        setup.timeframe,
        h_15m=settings.risk_max_hold_hours_15m,
        h_1h=settings.risk_max_hold_hours_1h,
        h_4h=settings.risk_max_hold_hours_4h,
        h_1d=settings.risk_max_hold_hours_1d,
    )

    # ATR is only used for trailing — fetch only when TP1 already hit to
    # avoid one DB roundtrip per active setup per candle in the common case.
    atr_value: float | None = None
    if setup.targets and setup.targets[0].get("hit_at") is not None:
        try:
            async with session_scope() as session:
                atr_value = await fetch_atr_for_trailing(
                    session,
                    symbol=setup.symbol,
                    timeframe=setup.timeframe,
                    candle_ts=candle_ts,
                )
        except Exception as exc:
            log.warning(
                "risk.atr_fetch_failed",
                setup_id=setup.id,
                symbol=setup.symbol,
                error=str(exc),
            )

    actions = compute_risk_actions(
        setup,
        close=close,
        candle_ts=candle_ts,
        atr_value=atr_value,
        move_to_be_after_r=settings.risk_move_to_be_after_r,
        trailing_atr_multiple=settings.risk_trailing_atr_multiple,
        max_hold_hours=max_hold,
    )
    if not actions:
        return setup

    # Apply all actions in one transaction. Idempotency lives in the SQL
    # (risk_state.breakeven_moved guard) so re-running on the same candle
    # is safe.
    new_sl = setup.stop_loss_px
    new_status = setup.status
    new_risk_state = dict(setup.risk_state or {})
    async with session_scope() as session:
        for action in actions:
            await apply_risk_action_to_db(
                session,
                setup_id=setup.id,
                action=action,
                candle_ts=candle_ts,
            )
            if isinstance(action, BreakevenAction):
                new_sl = action.new_sl
                new_risk_state["breakeven_moved"] = True
                log.info(
                    "risk.be_moved",
                    setup_id=setup.id,
                    symbol=setup.symbol,
                    new_sl=action.new_sl,
                    unrealized_r=action.unrealized_r,
                )
            elif isinstance(action, TrailingAction):
                new_sl = action.new_sl
                new_risk_state["trailing_active"] = True
                new_risk_state["trailing_sl"] = action.new_sl
                log.info(
                    "risk.trailing_updated",
                    setup_id=setup.id,
                    symbol=setup.symbol,
                    new_sl=action.new_sl,
                    atr=action.atr_value,
                )
            elif isinstance(action, TimeStopAction):
                new_status = "closed"
                new_risk_state["time_stopped"] = True
                log.info(
                    "risk.time_stopped",
                    setup_id=setup.id,
                    symbol=setup.symbol,
                    exit_px=action.exit_px,
                    held_hours=action.held_hours,
                )

    return setup.model_copy(
        update={
            "stop_loss_px": new_sl,
            "status": new_status,
            "risk_state": new_risk_state,
        }
    )


async def _evaluate_setup(
    setup: OpenSetupRow,
    *,
    high: float,
    low: float,
    close: float,
    candle_ts: datetime,
) -> None:
    """Aplica la transición correspondiente para UN setup. Cada setup en su
    propia transacción para que un fallo en uno no rompa los demás.

    NOTA: la evaluación de invalidation_conditions cuyo `spec.timeframe`
    coincide con el TF de esta vela ocurre en `_evaluate_close` (necesita
    el panel construido sobre el (symbol, tf) compartido). Aquí solo
    cubrimos expiry + entry/SL/TP — i.e. condiciones del propio TF del
    setup quedan a cargo del caller.

    B.1: para setups activos, primero invocamos al RiskManager (BE move /
    trailing / time stop). Si time-stop cerró el setup, terminamos aquí —
    el SL/TP check sobre un setup ya `closed` sería un no-op pero evitamos
    el roundtrip.
    """
    if setup.stop_loss_px is None:
        return

    # B.1: RiskManager hook (solo aplica a setups active). Pre-empt SL/TP
    # con BE move, trailing y time-stop. Retorna un setup posiblemente
    # mutado (nuevo stop_loss_px, posiblemente status=closed por time-stop).
    if setup.status == "active":
        setup = await _apply_risk_manager(setup, close=close, candle_ts=candle_ts)
        if setup.status != "active":
            return  # time-stopped — la transición terminal ya quedó persistida

    # Pending: PRIMERO chequear expiry wall-clock (cheap — sin panel),
    # LUEGO entry hit. Las invalidation_conditions ya fueron evaluadas
    # en `_evaluate_close` antes de invocar este helper, lo cual significa
    # que si llegamos aquí, ninguna condición de este TF disparó.
    if setup.status == "pending":
        if await _check_expiry_and_invalidate(setup, now_ts=candle_ts):
            return
        if not _entry_hit(setup.side, setup.entry_px, high, low):
            return
        # C.3 Blocker 1 + audit fix: scout proposals require an explicit
        # `approved` event before the runtime activates them. To close the race
        # with a concurrent `/setups/{id}/reject` (or `/cancel`), we lock the
        # row with SELECT FOR UPDATE inside the same transaction as the
        # approval check AND the transition. A concurrent reject will block
        # on the row lock until our commit; once we release, its UPDATE finds
        # status='active' and its `WHERE status='pending'` guard fails, so it
        # returns 0 rows affected and 409 (no double-cancel of an active setup).
        async with session_scope() as session:
            current_status = (
                await session.execute(
                    text(
                        "SELECT status FROM journal_trades "
                        "WHERE id = CAST(:tid AS uuid) FOR UPDATE"
                    ),
                    {"tid": setup.id},
                )
            ).scalar_one_or_none()
            if current_status != "pending":
                # Someone (user reject, another worker, manual cancel) moved
                # the setup out of pending between list_open_setups() and now.
                log.info(
                    "setup.pending_status_changed",
                    setup_id=setup.id,
                    observed_status=current_status,
                )
                return
            if setup.source == "scout_proposal":
                approved = await has_approval_event(session, trade_id=setup.id)
                if not approved:
                    log.info(
                        "setup.scout_pending_unapproved",
                        setup_id=setup.id,
                        symbol=setup.symbol,
                        timeframe=setup.timeframe,
                    )
                    return
            await transition_status(
                session,
                trade_id=setup.id,
                new_status="active",
                event="entry_hit",
                candle_ts=candle_ts,
                payload={"entry": setup.entry_px, "high": high, "low": low},
            )
        log.info(
            "setup.entry_hit",
            setup_id=setup.id,
            symbol=setup.symbol,
            timeframe=setup.timeframe,
            side=setup.side,
        )
        # Dispara review post-entry. Pasamos el `entry_px` como current_price
        # (la review evalúa el estado AL momento del entry; el precio actual
        # del market puede divergir un poco pero el agente leerá fresh data
        # vía las tools). Actualizamos el snapshot con entry_hit_at=candle_ts
        # para que el dispatcher pueda calcular hours_since_entry correctly.
        active_setup = setup.model_copy(
            update={"status": "active", "entry_hit_at": candle_ts}
        )
        _fire_review(
            setup=active_setup,
            trigger_kind="entry_hit",
            trigger_payload={
                "entry_px": setup.entry_px,
                "candle_high": high,
                "candle_low": low,
                "candle_ts": candle_ts.isoformat(),
            },
            current_price=setup.entry_px,
            candle_ts=candle_ts,
        )
        return

    # Active: SL prevalece sobre TP en caso de toque mutuo (fill conservador).
    if setup.status == "active":
        # `stop_loss_px` was checked non-None at the top, and `_apply_risk_manager`
        # (called above) preserves that invariant (BE/trailing only ratchet the SL).
        # The assert helps mypy re-narrow after the model_copy round-trip.
        assert setup.stop_loss_px is not None
        if _sl_hit(setup.side, setup.stop_loss_px, high, low):
            async with session_scope() as session:
                await transition_status(
                    session,
                    trade_id=setup.id,
                    new_status="closed",
                    event="sl_hit",
                    candle_ts=candle_ts,
                    payload={"sl": setup.stop_loss_px, "exit_px": setup.stop_loss_px},
                    exit_px=setup.stop_loss_px,
                    r_multiple=-1.0,
                )
            log.info(
                "setup.sl_hit",
                setup_id=setup.id,
                symbol=setup.symbol,
                timeframe=setup.timeframe,
            )
            # F5.5: dispara el post-mortem agent (fire-and-forget). El
            # dispatcher es idempotente (UNIQUE trade_id) y respeta el flag
            # settings.post_mortem_enabled — si está off no hace nada.
            _fire_post_mortem(
                trade_id=setup.id,
                user_id=setup.user_id,
                trigger_kind="setup_closed_sl",
                candle_ts=candle_ts,
            )
            return

        # TPs: chequeamos cada uno y marcamos hit_at en orden.
        targets = list(setup.targets)
        any_hit_now = False
        for t in targets:
            if t.get("hit_at") is not None:
                continue
            price = float(t.get("price", 0.0))
            if price <= 0:
                continue
            if _tp_hit(setup.side, price, high, low):
                t["hit_at"] = candle_ts.isoformat()
                any_hit_now = True

        if not any_hit_now:
            return

        all_hit = all(t.get("hit_at") for t in targets)
        last_hit_t = next(
            (t for t in reversed(targets) if t.get("hit_at")),
            None,
        )

        async with session_scope() as session:
            if all_hit and last_hit_t is not None:
                last_price = float(last_hit_t["price"])
                # See SL-branch above for the rationale on this assert.
                assert setup.stop_loss_px is not None
                r = _r_multiple(setup.side, setup.entry_px, setup.stop_loss_px, last_price)
                await transition_status(
                    session,
                    trade_id=setup.id,
                    new_status="closed",
                    event="tp_hit",
                    candle_ts=candle_ts,
                    payload={
                        "exit_px": last_price,
                        "label": last_hit_t.get("label"),
                        "all_targets_hit": True,
                    },
                    exit_px=last_price,
                    r_multiple=r,
                    targets_update=targets,
                )
                log.info(
                    "setup.tp_close",
                    setup_id=setup.id,
                    symbol=setup.symbol,
                    r_multiple=round(r, 3),
                )
                # F5.5: dispara el post-mortem agent (fire-and-forget).
                _fire_post_mortem(
                    trade_id=setup.id,
                    user_id=setup.user_id,
                    trigger_kind="setup_closed_tp",
                    candle_ts=candle_ts,
                )
            elif last_hit_t is not None:
                # Partial: solo marcamos hit_at, no cerramos.
                await update_targets_hits(
                    session,
                    trade_id=setup.id,
                    targets_update=targets,
                    candle_ts=candle_ts,
                    hit_label=str(last_hit_t.get("label", "TP")),
                    hit_price=float(last_hit_t["price"]),
                )
                log.info(
                    "setup.tp_partial",
                    setup_id=setup.id,
                    label=last_hit_t.get("label"),
                )
                # Dispara review tras TP parcial — útil para decidir trail SL
                # o tomar parcial extra. NO disparamos en tp final (above)
                # porque el setup ya es terminal.
                hit_price = float(last_hit_t["price"])
                active_setup_partial = setup.model_copy(
                    update={"targets": targets}
                )
                _fire_review(
                    setup=active_setup_partial,
                    trigger_kind="tp_partial",
                    trigger_payload={
                        "tp_label": last_hit_t.get("label"),
                        "tp_price": hit_price,
                        "remaining_tps": sum(
                            1 for t in targets if t.get("hit_at") is None
                        ),
                    },
                    current_price=hit_price,
                    candle_ts=candle_ts,
                )


# ---------------------------------------------------------------------------
# Per-(symbol, tf) market loop
# ---------------------------------------------------------------------------


async def _evaluate_close(
    *,
    symbol: str,
    timeframe: str,
    high: float,
    low: float,
    close: float,
    candle_ts: datetime,
) -> None:
    async with session_scope() as session:
        all_open = await list_open_setups(session)
    sym_upper = symbol.upper()

    # ===== Step 1: evaluate invalidation_conditions whose spec is anchored
    # to THIS (symbol, timeframe). These can affect ANY pending setup
    # regardless of the setup's own (symbol, tf) — so a 1h-anchored setup
    # invalidated by a 4h-close-break condition fires when the 4h candle
    # closes (this branch). Cross-symbol conditions also flow here (e.g. a
    # BTCUSDT condition on an altcoin idea fires when BTC's candle closes).
    pending_setups = [s for s in all_open if s.status == "pending"]
    affected_pending: dict[str, list[tuple[int, RuleSpec]]] = {}
    for s in pending_setups:
        for i, spec in enumerate(_parse_conditions(s)):
            if spec.symbol.upper() == sym_upper and spec.timeframe == timeframe:
                affected_pending.setdefault(s.id, []).append((i, spec))

    invalidated_ids: set[str] = set()
    if affected_pending:
        # Compute the panel ONCE for this (symbol, tf) covering the union
        # of indicators across all matched specs. Reused for every condition.
        all_specs: list[RuleSpec] = [
            spec for pairs in affected_pending.values() for _, spec in pairs
        ]
        async with session_scope() as session:
            panel = await compute_panel_for_specs(
                session,
                symbol=sym_upper,
                timeframe=timeframe,
                specs=all_specs,
                until=candle_ts,
            )
        if panel.height > 0:
            setup_by_id = {s.id: s for s in pending_setups}
            for setup_id, pairs in affected_pending.items():
                setup = setup_by_id.get(setup_id)
                if setup is None:
                    continue
                for idx, spec in pairs:
                    try:
                        fired = await _check_conditions_and_invalidate(
                            setup,
                            candle_ts=candle_ts,
                            panel=panel,
                            condition_index=idx,
                            spec=spec,
                        )
                    except Exception as exc:
                        log.exception(
                            "setup.condition.error",
                            setup_id=setup.id,
                            error=str(exc),
                        )
                        continue
                    if fired:
                        invalidated_ids.add(setup.id)
                        break  # OR semantics — first match wins

    # ===== Step 2: classic entry/SL/TP evaluation for setups whose OWN
    # (symbol, tf) matches this candle. Skip any setup that was just
    # invalidated in step 1 (no entry_hit on an invalidated setup).
    own_tf_setups = [
        s
        for s in all_open
        if s.symbol.upper() == sym_upper
        and s.timeframe == timeframe
        and s.id not in invalidated_ids
    ]
    if not own_tf_setups:
        return
    for setup in own_tf_setups:
        try:
            await _evaluate_setup(
                setup, high=high, low=low, close=close, candle_ts=candle_ts
            )
        except Exception as exc:
            log.exception(
                "setup.evaluate.error",
                setup_id=setup.id,
                error=str(exc),
            )

    # ===== Step 3: price-based review triggers para setups que SIGUEN active
    # tras el step 2. Re-leemos open setups para tener last_review_price/
    # last_review_at frescos y filtramos active+(symbol, tf) match. Setups
    # que acaban de transicionar a active en este tick disparan en el hook
    # de entry_hit (no aquí) — chequeo de status reciente evita duplicar.
    await _evaluate_price_review_triggers(
        symbol=sym_upper,
        timeframe=timeframe,
        close=close,
        candle_ts=candle_ts,
    )


# ---------------------------------------------------------------------------
# F3: price-based + approaching-SL triggers
# ---------------------------------------------------------------------------


async def _evaluate_price_review_triggers(
    *,
    symbol: str,
    timeframe: str,
    close: float,
    candle_ts: datetime,
) -> None:
    """Recorre setups ACTIVE de este (symbol, tf) y dispara reviews por:

    - **price_move**: |close - entry| / entry ≥ umbral % (default 2%).
      Guard adicional: |close - last_review_price| / last_review_price ≥
      0.5% (evita re-firing oscilando cerca del threshold).

    - **approaching_sl**: el precio cubrió ≥ fraction del camino entry→SL
      (default 0.75, i.e. dentro del 25% final). Solo si la review previa
      no fue ya `approaching_sl` (evita repetir mismo kind back-to-back).

    Esta función NO compite con el hook de entry_hit (que también firea
    `_fire_review`) — un setup recién transitado a `active` en este tick
    todavía no tiene `entry_hit_at` cargado en `setup` (la copia de
    list_open_setups era pre-transition), pero el cooldown del dispatcher
    bloquea el segundo disparo cuando llega.
    """
    settings = get_settings()
    async with session_scope() as session:
        all_open = await list_open_setups(session)
    actives = [
        s
        for s in all_open
        if s.symbol.upper() == symbol
        and s.timeframe == timeframe
        and s.status == "active"
    ]
    if not actives:
        return

    # Necesitamos last_review_price y last_review_at per-setup para los
    # guards. Hacemos una sola query batch.
    last_review_meta = await _fetch_review_meta([s.id for s in actives])

    for setup in actives:
        meta = last_review_meta.get(setup.id, {})
        last_review_price = meta.get("last_review_price")

        # --- price_move ---------------------------------------------------
        if setup.entry_px:
            pct_from_entry = abs(close - setup.entry_px) / setup.entry_px * 100.0
            threshold_crossed = pct_from_entry >= settings.review_price_move_pct
            # Guard contra oscilación: si ya hubo review reciente cerca
            # de este precio (< 0.5% delta), skip — el cooldown del
            # dispatcher lo cubriría también pero esto evita el log noise.
            far_enough_from_last = last_review_price is None or (
                abs(close - last_review_price) / max(last_review_price, 1e-9)
                >= 0.005
            )
            if threshold_crossed and far_enough_from_last:
                    direction_favorable = (
                        (setup.side == "long" and close > setup.entry_px)
                        or (setup.side == "short" and close < setup.entry_px)
                    )
                    _fire_review(
                        setup=setup,
                        trigger_kind="price_move",
                        trigger_payload={
                            "close": close,
                            "pct_from_entry": round(pct_from_entry, 3),
                            "direction_favorable": direction_favorable,
                        },
                        current_price=close,
                        candle_ts=candle_ts,
                    )
                    continue  # un solo trigger por setup por tick

        # --- approaching_sl ----------------------------------------------
        if setup.stop_loss_px is not None and setup.entry_px:
            risk = abs(setup.entry_px - setup.stop_loss_px)
            if risk > 0:
                # Distance del precio actual al SL relativa al riesgo total.
                # sl_distance_fraction = 0.0 → precio en SL. 1.0 → precio en entry.
                if setup.side == "long":
                    travel_to_sl = setup.entry_px - close  # >0 si bajó
                else:
                    travel_to_sl = close - setup.entry_px
                sl_path_covered = max(travel_to_sl, 0.0) / risk  # 0..1
                if sl_path_covered >= settings.review_approaching_sl_pct:
                    _fire_review(
                        setup=setup,
                        trigger_kind="approaching_sl",
                        trigger_payload={
                            "close": close,
                            "sl_path_covered_fraction": round(sl_path_covered, 3),
                        },
                        current_price=close,
                        candle_ts=candle_ts,
                    )


async def _fetch_review_meta(trade_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Batch fetch de last_review_price/last_review_at para guards."""
    if not trade_ids:
        return {}
    from sqlalchemy import text as _text

    async with session_scope() as session:
        rows = (
            await session.execute(
                _text(
                    """
                    SELECT id::text AS id, last_review_at, last_review_price
                    FROM journal_trades
                    WHERE id = ANY(CAST(:ids AS uuid[]))
                    """
                ),
                {"ids": trade_ids},
            )
        ).mappings().all()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        out[r["id"]] = {
            "last_review_at": r["last_review_at"],
            "last_review_price": (
                float(r["last_review_price"])
                if r["last_review_price"] is not None
                else None
            ),
        }
    return out


# ---------------------------------------------------------------------------
# F3: time-based review scheduler
# ---------------------------------------------------------------------------


_REVIEW_SCHEDULE_INTERVAL_S = 300.0  # 5 min


async def _review_scheduler_once() -> int:
    """Lee setups active cuyo `next_review_at <= now()` y dispara
    `time_elapsed` para cada uno. El dispatcher actualizará `next_review_at`
    al siguiente offset tras persistir la review.

    Returns the count of triggers dispatched (logging-only)."""
    now_utc = datetime.now(tz=UTC)
    async with session_scope() as session:
        rows = await list_active_setups_due_for_time_review(session, now=now_utc)
    if not rows:
        return 0

    n_fired = 0
    for r in rows:
        try:
            setup = OpenSetupRow(
                id=r["id"],
                user_id=r["user_id"],
                symbol=r["symbol"],
                timeframe=r["timeframe"],
                side=r["side"],
                status=r["status"],
                entry_px=float(r["entry_px"]),
                stop_loss_px=(
                    float(r["stop_loss_px"])
                    if r.get("stop_loss_px") is not None
                    else None
                ),
                targets=list(r.get("targets") or []),
                invalidation_conditions=[],
                expires_at=None,
                proposed_at=r.get("proposed_at"),
                entry_hit_at=r.get("entry_hit_at"),
                regime=r.get("regime"),
                confidence=r.get("confidence"),
                summary_es_full=r.get("summary_es_full"),
                confluences=list(r.get("confluences") or []),
                scenarios=list(r.get("scenarios") or []),
            )
        except Exception as exc:
            log.warning("review.scheduler.row_skip", error=str(exc), row_id=r.get("id"))
            continue

        # Compute hours_since_entry para el payload.
        if setup.entry_hit_at is not None:
            hours_since_entry = round(
                (now_utc - setup.entry_hit_at).total_seconds() / 3600.0, 2
            )
        else:
            hours_since_entry = 0.0

        # El precio actual lo dejamos como NaN-safe del entry — el agente
        # leerá fresh OHLCV vía tools. El campo se usa solo para journal,
        # no para decisión del agente.
        _fire_review(
            setup=setup,
            trigger_kind="time_elapsed",
            trigger_payload={
                "hours_since_entry": hours_since_entry,
                "scheduled_at": (r.get("next_review_at") or now_utc).isoformat()
                if hasattr(r.get("next_review_at") or now_utc, "isoformat")
                else None,
            },
            current_price=setup.entry_px,
            candle_ts=now_utc,
        )
        n_fired += 1
    if n_fired:
        log.info("review.scheduler.fired", count=n_fired)
    return n_fired


async def _review_scheduler_loop() -> None:
    log.info("review.scheduler.start", interval_s=_REVIEW_SCHEDULE_INTERVAL_S)
    while True:
        try:
            await asyncio.sleep(_REVIEW_SCHEDULE_INTERVAL_S)
            await _review_scheduler_once()
        except asyncio.CancelledError:
            log.info("review.scheduler.cancelled")
            raise
        except Exception as exc:
            log.exception("review.scheduler.error", error=str(exc))


async def _market_loop(symbol: str, timeframe: str) -> None:
    channel = market_channel(exchange=EXCHANGE_NAME, symbol=symbol, timeframe=timeframe)
    log.info("setup.market_loop.start", channel=channel)
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
                        ts_raw = data.get("ts")
                        candle_ts = (
                            datetime.fromisoformat(ts_raw)
                            if isinstance(ts_raw, str)
                            else datetime.utcnow()
                        )
                        high = float(data.get("h", 0.0))
                        low = float(data.get("l", 0.0))
                        close = float(data.get("c", 0.0))
                        if high <= 0 or low <= 0 or close <= 0:
                            continue
                        await _evaluate_close(
                            symbol=symbol,
                            timeframe=timeframe,
                            high=high,
                            low=low,
                            close=close,
                            candle_ts=candle_ts,
                        )
                    except Exception as exc:
                        log.exception(
                            "setup.evaluate_close.error",
                            symbol=symbol,
                            timeframe=timeframe,
                            error=str(exc),
                        )
        except asyncio.CancelledError:
            log.info("setup.market_loop.cancelled", channel=channel)
            raise
        except Exception as exc:
            log.warning(
                "setup.market_loop.error", channel=channel, error=str(exc)
            )
            await asyncio.sleep(2.0)


# ---------------------------------------------------------------------------
# Lifecycle owner
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Wall-clock expiry sweeper
# ---------------------------------------------------------------------------


_EXPIRY_SWEEP_INTERVAL_S = 60.0


async def _expiry_sweep_once() -> int:
    """Single pass over all pending setups with `expires_at` set; invalidate
    any whose deadline has passed. Returns the count of newly invalidated
    setups (for logging)."""
    now_utc = datetime.now(tz=UTC)
    async with session_scope() as session:
        rows = await list_pending_with_expiry(session)
    if not rows:
        return 0
    n_fired = 0
    for r in rows:
        expires_at = r["expires_at"]
        if expires_at is None or expires_at > now_utc:
            continue
        async with session_scope() as session:
            ok = await transition_to_invalidated(
                session,
                trade_id=r["id"],
                candle_ts=now_utc,
                payload={
                    "reason": "expires_at",
                    "expires_at": expires_at.isoformat(),
                    "now": now_utc.isoformat(),
                    "source": "expiry_sweeper",
                },
            )
        if ok:
            n_fired += 1
            log.info(
                "setup.invalidated_expiry_sweep",
                setup_id=r["id"],
                symbol=r["symbol"],
                expires_at=expires_at.isoformat(),
            )
    return n_fired


async def _expiry_sweep_loop() -> None:
    """Periodic sweep so slow-TF setups (4h/1d) don't wait hours for the
    next candle close before their wall-clock expiry can be honored."""
    log.info("setup.expiry_sweep.start", interval_s=_EXPIRY_SWEEP_INTERVAL_S)
    while True:
        try:
            await asyncio.sleep(_EXPIRY_SWEEP_INTERVAL_S)
            await _expiry_sweep_once()
        except asyncio.CancelledError:
            log.info("setup.expiry_sweep.cancelled")
            raise
        except Exception as exc:
            log.exception("setup.expiry_sweep.error", error=str(exc))


class SetupRuntime:
    """Mirror de AlertsRuntime — start/stop desde FastAPI lifespan."""

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task[Any]] = []

    async def start(self) -> None:
        if self._tasks:
            return
        # Una task por (symbol, tf) en la watchlist, igual que alerts.
        # 1m queda fuera (los setups no se emiten ahí).
        for symbol, tf in get_watch_list():
            if tf not in _WATCHED_TIMEFRAMES:
                continue
            t = asyncio.create_task(
                _market_loop(symbol, tf), name=f"setups:{symbol}:{tf}"
            )
            self._tasks.append(t)
        # Wall-clock sweeper — covers expires_at falling between candle
        # closes on slow TFs.
        sweep_task = asyncio.create_task(
            _expiry_sweep_loop(), name="setups:expiry_sweep"
        )
        self._tasks.append(sweep_task)
        # Review scheduler — dispara `time_elapsed` para setups active cuyo
        # next_review_at venció (4h, 24h, 72h desde entry_hit_at por defecto).
        review_scheduler_task = asyncio.create_task(
            _review_scheduler_loop(), name="setups:review_scheduler"
        )
        self._tasks.append(review_scheduler_task)
        log.info("setup.runtime.start", n_market_loops=len(self._tasks) - 2)

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self._tasks.clear()
        log.info("setup.runtime.stop")
