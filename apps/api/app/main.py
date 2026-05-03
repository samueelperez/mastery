import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.api.ohlcv import router as ohlcv_router
from app.api.ws import router as ws_router
from app.broadcasting.pubsub import close_client as close_valkey
from app.config import get_settings
from app.db import dispose_engine, init_engine
from app.ingestion.live_klines import LiveIngestion

logging.basicConfig(level=logging.INFO, format="%(message)s")
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)
log = structlog.get_logger()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_engine()
    ingestion = LiveIngestion()
    await ingestion.start()
    log.info("api.start")
    try:
        yield
    finally:
        log.info("api.stop")
        await ingestion.stop()
        await close_valkey()
        await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Trading Copilot API",
        version="0.0.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # AI SDK v6 needs to read these to detect the data-stream protocol.
        expose_headers=["x-vercel-ai-ui-message-stream", "content-type"],
    )
    app.include_router(health_router)
    app.include_router(ohlcv_router)
    app.include_router(ws_router)
    app.include_router(chat_router)
    return app


app = create_app()
