from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app.db import session_scope

router = APIRouter()


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    db: Literal["ok", "fail"]
    valkey: Literal["ok", "fail", "skip"]


@router.get("/health", response_model=HealthResponse, tags=["meta"])
async def health() -> HealthResponse:
    db_ok = False
    try:
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    # Valkey check is best-effort; leave as 'skip' until we wire the client (F0.7).
    valkey_status: Literal["ok", "fail", "skip"] = "skip"

    overall: Literal["ok", "degraded"] = "ok" if db_ok else "degraded"
    return HealthResponse(
        status=overall,
        db="ok" if db_ok else "fail",
        valkey=valkey_status,
    )
