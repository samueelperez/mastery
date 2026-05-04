"""POST /chat — Vercel AI SDK Data Stream Protocol endpoint.

Wires Pydantic AI's `VercelAIAdapter` to FastAPI; the adapter handles the SSE
encoding (text-delta, reasoning-delta, tool-input-available, tool-output-available,
finish, error) so we don't have to. Auth: the request's BetterAuth session cookie
resolves to a user_id which flows into AgentDeps so every tool can scope writes
to the authenticated user.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Request, Response
from pydantic_ai.ui.vercel_ai import VercelAIAdapter

from app.agent.agent import get_agent
from app.agent.deps import AgentDeps
from app.auth import require_user_id
from app.db import session_scope

router = APIRouter()
log = structlog.get_logger("api.chat")


@router.post("/chat", tags=["agent"])
async def chat(
    request: Request,
    user_id: Annotated[str, Depends(require_user_id)],
) -> Response:
    deps = AgentDeps(
        session_factory=session_scope,
        log=structlog.get_logger("agent.run"),
        user_id=user_id,
    )
    log.info("chat.request.start", user_id=user_id)
    return await VercelAIAdapter.dispatch_request(
        request,
        agent=get_agent(),
        deps=deps,
    )
