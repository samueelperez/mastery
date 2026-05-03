from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app.broadcasting.pubsub import ping as valkey_ping
from app.db import session_scope

router = APIRouter()


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    db: Literal["ok", "fail"]
    valkey: Literal["ok", "fail"]


@router.get("/health", response_model=HealthResponse, tags=["meta"])
async def health() -> HealthResponse:
    db_ok = False
    try:
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    valkey_ok = await valkey_ping()

    overall: Literal["ok", "degraded"] = "ok" if (db_ok and valkey_ok) else "degraded"
    return HealthResponse(
        status=overall,
        db="ok" if db_ok else "fail",
        valkey="ok" if valkey_ok else "fail",
    )
