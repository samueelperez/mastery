"""Runtime dependencies passed to every tool via Pydantic AI's RunContext."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

import structlog
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class AgentDeps:
    """Lightweight bag of injectables — keeps tools stateless and testable.

    `session_factory` is a callable that returns an async context manager
    yielding an AsyncSession. The canonical wiring uses `app.db.session_scope`
    (decorated with @asynccontextmanager) so `async with session_factory()` works.
    """

    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]]
    log: structlog.BoundLogger
    user_id: str
    exchange: str = "binance_usdm"
