from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app.broadcasting.pubsub import ping as valkey_ping
from app.config import get_settings
from app.db import session_scope

router = APIRouter()


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    db: Literal["ok", "fail"]
    valkey: Literal["ok", "fail"]
    openrouter: Literal["configured", "missing"]
    voyage: Literal["configured", "missing"]


@router.get("/health", response_model=HealthResponse, tags=["meta"])
async def health() -> HealthResponse:
    """Lightweight liveness + config check — does NOT make outbound API calls
    to OpenRouter / Voyage (those are paid). Just verifies the keys are set,
    so a deploy with missing secrets shows up amber on the navbar pill instead
    of looking healthy until the agent's first request fails."""
    db_ok = False
    try:
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    valkey_ok = await valkey_ping()
    settings = get_settings()
    openrouter_set = bool(settings.openrouter_api_key)
    voyage_set = bool(settings.voyage_api_key)

    overall: Literal["ok", "degraded"] = (
        "ok"
        if (db_ok and valkey_ok and openrouter_set and voyage_set)
        else "degraded"
    )
    return HealthResponse(
        status=overall,
        db="ok" if db_ok else "fail",
        valkey="ok" if valkey_ok else "fail",
        openrouter="configured" if openrouter_set else "missing",
        voyage="configured" if voyage_set else "missing",
    )
