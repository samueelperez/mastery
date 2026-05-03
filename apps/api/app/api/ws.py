"""WebSocket fanout from Valkey pub/sub to browser clients.

Endpoint: /ws/market?symbol=BTCUSDT&tf=1m
Client receives one JSON message per kline update (every WS tick from Binance).
"""

from __future__ import annotations

import asyncio
import contextlib

import orjson
import structlog
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

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
