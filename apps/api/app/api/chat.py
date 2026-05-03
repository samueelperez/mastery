"""POST /chat — Vercel AI SDK Data Stream Protocol endpoint.

Wires Pydantic AI's `VercelAIAdapter` to FastAPI; the adapter handles the SSE
encoding (text-delta, reasoning-delta, tool-input-available, tool-output-available,
finish, error) so we don't have to.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request, Response
from pydantic_ai.ui.vercel_ai import VercelAIAdapter

from app.agent.agent import get_agent
from app.agent.deps import AgentDeps
from app.db import session_scope

router = APIRouter()
log = structlog.get_logger("api.chat")


@router.post("/chat", tags=["agent"])
async def chat(request: Request) -> Response:
    deps = AgentDeps(
        session_factory=session_scope,
        log=structlog.get_logger("agent.run"),
    )
    log.info("chat.request.start")
    return await VercelAIAdapter.dispatch_request(
        request,
        agent=get_agent(),
        deps=deps,
    )
