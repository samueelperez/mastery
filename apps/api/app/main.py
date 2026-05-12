import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.alerts.routes import router as alerts_router
from app.alerts.runtime import AlertsRuntime
from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.api.metrics import router as metrics_router
from app.api.notifications import router as notifications_router
from app.api.setups import router as setups_router
from app.backtest.routes_backtests import router as backtests_router
from app.backtest.routes_strategies import router as strategies_router
from app.core.broadcasting.pubsub import close_client as close_valkey
from app.core.config import get_settings
from app.core.db import dispose_engine, init_engine
from app.journal.routes import router as journal_router
from app.market.ohlcv.ingestion_live import LiveIngestion
from app.market.ohlcv.routes import router as ohlcv_router
from app.market.ws_routes import router as ws_router
from app.runtime.setup_runtime import SetupRuntime

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
    alerts = AlertsRuntime()
    setups = SetupRuntime()
    await ingestion.start()
    await alerts.start()
    await setups.start()
    log.info("api.start")
    try:
        yield
    finally:
        log.info("api.stop")
        await setups.stop()
        await alerts.stop()
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
    app.include_router(metrics_router)
    app.include_router(ohlcv_router)
    app.include_router(ws_router)
    app.include_router(chat_router)
    app.include_router(backtests_router)
    app.include_router(strategies_router)
    app.include_router(journal_router)
    app.include_router(alerts_router)
    app.include_router(setups_router)
    app.include_router(notifications_router)
    return app


app = create_app()
