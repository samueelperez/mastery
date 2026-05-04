"""WebSocket fanout from Valkey pub/sub to browser clients.

Endpoints:
  /ws/market?symbol=BTCUSDT&tf=1m   - one JSON per kline tick from Binance
  /ws/alerts?user_id=me             - one JSON per alert_event fired

Both follow the same pattern: subscribe to the Valkey channel, forward each
message to the browser. The alerts channel is populated by `app.alerts.runtime`.
"""

from __future__ import annotations

import asyncio
import contextlib

import orjson
import structlog
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.alerts.runtime import alerts_channel
from app.broadcasting.pubsub import market_channel, subscribe
from app.data.binance_adapter import EXCHANGE_NAME

log = structlog.get_logger(__name__)
router = APIRouter()


@router.websocket("/ws/market")
async def market_ws(
    websocket: WebSocket,
    symbol: str = Query(..., min_length=1, max_length=32),
    tf: str = Query(..., alias="tf", min_length=1, max_length=8),
) -> None:
    await websocket.accept()
    channel = market_channel(exchange=EXCHANGE_NAME, symbol=symbol, timeframe=tf)
    log.info("ws.client.connect", channel=channel)

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


@router.websocket("/ws/alerts")
async def alerts_ws(
    websocket: WebSocket,
    user_id: str = Query(default="me", min_length=1, max_length=64),
) -> None:
    """Fan-out for alert_events. The runtime publishes to
    `alerts:user:{user_id}` whenever a rule fires or a high-severity bias is
    promoted; this endpoint forwards as `{type: 'alert_event', data: ...}`."""
    await websocket.accept()
    channel = alerts_channel(user_id)
    log.info("ws.alerts.connect", channel=channel)

    try:
        async with subscribe(channel) as pubsub:
            await websocket.send_json({"type": "subscribed", "channel": channel})
            while True:
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=30.0
                )
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
