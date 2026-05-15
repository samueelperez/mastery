"""WebSocket fanout from Valkey pub/sub to browser clients.

Endpoints (todos auth-gated):
  /ws/market?symbol=BTCUSDT&tf=1m   - one JSON per kline tick from Binance
  /ws/alerts                        - one JSON per alert_event fired
  /ws/reviews                       - TradeReview events

Browsers no aceptan Authorization header en WS; el cliente pasa el token via
query param `?token=…` cuando estamos en cross-domain (Vercel ↔ Railway) o
via cookie cuando es same-origin. El token en query string puede aparecer en
access logs de proxies/CDN intermedios — minimizado en logs internos (sin
token_prefix). Para F4+ considerar mover a primer frame WS o subprotocol.
"""

from __future__ import annotations

import asyncio
import contextlib

import orjson
import structlog
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from app.alerts.runtime import alerts_channel
from app.core.auth.session import (
    SESSION_COOKIE_NAME,
    extract_session_token,
    lookup_user_id_for_token,
)
from app.core.broadcasting.pubsub import market_channel, reviews_channel, subscribe
from app.core.db import session_scope
from app.core.exchanges.binance_adapter import EXCHANGE_NAME

log = structlog.get_logger(__name__)
router = APIRouter()


async def _ws_user_id(websocket: WebSocket) -> str | None:
    """Resolve the BetterAuth session on a WebSocket. Browsers no aceptan
    Authorization header en WS, así que el cliente pasa el token via query
    param `?token=…` cuando estamos en cross-domain (Vercel ↔ Railway).
    Si no hay query param, fallback a la cookie (dev local same-origin).

    En ambos transports el token llega con sufijo HMAC (`<token>.<hmac>`);
    `extract_session_token` recorta el sufijo igual que para la cookie."""
    token: str | None = None
    raw_query_token = websocket.query_params.get("token")
    if raw_query_token:
        token = extract_session_token(raw_query_token)
    if token is None:
        token = extract_session_token(websocket.cookies.get(SESSION_COOKIE_NAME))
    if token is None:
        return None
    async with session_scope() as session:
        return await lookup_user_id_for_token(token, session)


@router.websocket("/ws/market")
async def market_ws(
    websocket: WebSocket,
    symbol: str = Query(..., min_length=1, max_length=32),
    tf: str = Query(..., alias="tf", min_length=1, max_length=8),
) -> None:
    # OHLCV es global (no per-user) pero exigimos auth para evitar DoS
    # trivial y mantener consistencia con /ws/alerts y /ws/reviews.
    user_id = await _ws_user_id(websocket)
    if user_id is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        log.info("ws.market.unauth")
        return
    await websocket.accept()
    channel = market_channel(exchange=EXCHANGE_NAME, symbol=symbol, timeframe=tf)
    log.info("ws.client.connect", channel=channel, user_id=user_id)

    try:
        async with subscribe(channel) as pubsub:
            # Forward an immediate "ready" frame so the client knows we're listening.
            await websocket.send_json({"type": "subscribed", "channel": channel})

            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=30.0)
                if msg is None:
                    # Heartbeat to keep proxies / load balancers from killing the conn.
                    await websocket.send_json({"type": "ping"})
                    continue
                if msg.get("type") != "message":
                    continue
                # `decode_responses=True` on the redis client gives us a str payload.
                data = orjson.loads(msg["data"])
                await websocket.send_json({"type": "kline", "data": data})
    except WebSocketDisconnect:
        log.info("ws.client.disconnect", channel=channel)
    except asyncio.CancelledError:
        log.info("ws.cancelled", channel=channel)
        raise
    except Exception as exc:
        log.warning("ws.error", channel=channel, error=str(exc))
        with contextlib.suppress(Exception):
            await websocket.close()


@router.websocket("/ws/reviews")
async def reviews_ws(websocket: WebSocket) -> None:
    """Fan-out de TradeReviews automáticas. El review_dispatcher publica en
    `reviews:user:{user_id}` cuando el review_agent emite un análisis post-
    entry; este endpoint lo reenvía como `{type: 'trade_review', data: ...}`.

    Canal separado de `/ws/alerts` para que el frontend pueda atar handlers
    distintos (chat injection vs alert panel). Mismo patrón de auth.
    """
    user_id = await _ws_user_id(websocket)
    if user_id is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        log.info("ws.reviews.unauth")
        return
    await websocket.accept()
    channel = reviews_channel(user_id)
    log.info("ws.reviews.connect", channel=channel, user_id=user_id)

    try:
        async with subscribe(channel) as pubsub:
            await websocket.send_json({"type": "subscribed", "channel": channel})
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=30.0)
                if msg is None:
                    await websocket.send_json({"type": "ping"})
                    continue
                if msg.get("type") != "message":
                    continue
                data = orjson.loads(msg["data"])
                await websocket.send_json({"type": "trade_review", "data": data})
    except WebSocketDisconnect:
        log.info("ws.reviews.disconnect", channel=channel)
    except asyncio.CancelledError:
        log.info("ws.reviews.cancelled", channel=channel)
        raise
    except Exception as exc:
        log.warning("ws.reviews.error", channel=channel, error=str(exc))
        with contextlib.suppress(Exception):
            await websocket.close()


@router.websocket("/ws/alerts")
async def alerts_ws(websocket: WebSocket) -> None:
    """Fan-out for alert_events. The runtime publishes to
    `alerts:user:{user_id}` whenever a rule fires or a high-severity bias is
    promoted; this endpoint forwards as `{type: 'alert_event', data: ...}`.
    user_id is resolved from the BetterAuth cookie, not query string."""
    user_id = await _ws_user_id(websocket)
    if user_id is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        log.info("ws.alerts.unauth")
        return
    await websocket.accept()
    channel = alerts_channel(user_id)
    log.info("ws.alerts.connect", channel=channel, user_id=user_id)

    try:
        async with subscribe(channel) as pubsub:
            await websocket.send_json({"type": "subscribed", "channel": channel})
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=30.0)
                if msg is None:
                    await websocket.send_json({"type": "ping"})
                    continue
                if msg.get("type") != "message":
                    continue
                data = orjson.loads(msg["data"])
                await websocket.send_json({"type": "alert_event", "data": data})
    except WebSocketDisconnect:
        log.info("ws.alerts.disconnect", channel=channel)
    except asyncio.CancelledError:
        log.info("ws.alerts.cancelled", channel=channel)
        raise
    except Exception as exc:
        log.warning("ws.alerts.error", channel=channel, error=str(exc))
        with contextlib.suppress(Exception):
            await websocket.close()
