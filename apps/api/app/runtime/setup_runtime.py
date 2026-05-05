"""Watcher de setups: aplica transitions automáticas al cierre de cada candle.

Un asyncio task por (symbol, timeframe) suscrito al mismo canal Valkey que el
ingestor (`mkt:{exchange}:{symbol}:k:{timeframe}`). En cada vela cerrada:

  1. Lee setups con status ∈ (pending, active) para ese (symbol, tf).
  2. Para cada setup:
     - pending → active si la vela toca el entry.
     - active → closed (sl_hit, r=-1) si la vela toca el SL.
     - active → marca TPs hit como hit_at; si TODOS los TPs están hechos,
       cierra como tp_hit con r_multiple del último TP.
  3. Edge case: si una misma vela toca SL y TP, prevalece SL (fill conservador).

Patrón calcado de `app.alerts.runtime` — mismo lifecycle (start/stop desde
FastAPI lifespan), mismo subscribe/timeout/reconnect.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime
from typing import Any

import orjson
import structlog

from app.broadcasting.pubsub import market_channel, subscribe
from app.data.binance_adapter import EXCHANGE_NAME
from app.db import session_scope
from app.ingestion.live_klines import get_watch_list
from app.storage.setup_repo import (
    OpenSetupRow,
    list_open_setups,
    transition_status,
    update_targets_hits,
)

log = structlog.get_logger(__name__)


# Timeframes que el watcher escucha. 1m queda fuera (demasiado ruido para
# F1; los setups del agente se emiten en 15m/1h/4h/1d).
_WATCHED_TIMEFRAMES = ("15m", "1h", "4h", "1d")


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


async def _evaluate_setup(
    setup: OpenSetupRow,
    *,
    high: float,
    low: float,
    candle_ts: datetime,
) -> None:
    """Aplica la transición correspondiente para UN setup. Cada setup en su
    propia transacción para que un fallo en uno no rompa los demás."""
    if setup.invalidation_px is None:
        return

    # Pending: solo evaluamos entry hit.
    if setup.status == "pending":
        if not _entry_hit(setup.side, setup.entry_px, high, low):
            return
        async with session_scope() as session:
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
        return

    # Active: SL prevalece sobre TP en caso de toque mutuo (fill conservador).
    if setup.status == "active":
        if _sl_hit(setup.side, setup.invalidation_px, high, low):
            async with session_scope() as session:
                await transition_status(
                    session,
                    trade_id=setup.id,
                    new_status="closed",
                    event="sl_hit",
                    candle_ts=candle_ts,
                    payload={"sl": setup.invalidation_px, "exit_px": setup.invalidation_px},
                    exit_px=setup.invalidation_px,
                    r_multiple=-1.0,
                )
            log.info(
                "setup.sl_hit",
                setup_id=setup.id,
                symbol=setup.symbol,
                timeframe=setup.timeframe,
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
                r = _r_multiple(setup.side, setup.entry_px, setup.invalidation_px, last_price)
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


# ---------------------------------------------------------------------------
# Per-(symbol, tf) market loop
# ---------------------------------------------------------------------------


async def _evaluate_close(
    *, symbol: str, timeframe: str, high: float, low: float, candle_ts: datetime
) -> None:
    async with session_scope() as session:
        all_open = await list_open_setups(session)
    matching = [
        s
        for s in all_open
        if s.symbol.upper() == symbol.upper() and s.timeframe == timeframe
    ]
    if not matching:
        return
    for setup in matching:
        try:
            await _evaluate_setup(setup, high=high, low=low, candle_ts=candle_ts)
        except Exception as exc:
            log.exception(
                "setup.evaluate.error",
                setup_id=setup.id,
                error=str(exc),
            )


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
                        if high <= 0 or low <= 0:
                            continue
                        await _evaluate_close(
                            symbol=symbol,
                            timeframe=timeframe,
                            high=high,
                            low=low,
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
        log.info("setup.runtime.start", n_market_loops=len(self._tasks))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self._tasks.clear()
        log.info("setup.runtime.stop")
