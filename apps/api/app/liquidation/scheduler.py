"""Background snapshot scheduler — populates ``liquidation_buckets`` densely.

``HeatmapService.get_snapshot()`` only persisted snapshots on-demand (when
the agent tool was invoked). The time-aware 2D heatmap UI needs DENSE
history, so this scheduler runs in the FastAPI lifespan and takes a
snapshot for each ``(symbol, timeframe)`` in ``WATCH_SYMBOLS x ('1h','4h','1d')``
every ``SCHEDULER_INTERVAL_S`` seconds, with per-pair jitter to spread load.

Storage / scoping
-----------------
Snapshots are persisted under :data:`SYSTEM_USER_ID` (UUID zero), not under
a real user's id. The liquidation data is market-wide — every user sees the
same zones — so storing per-user would just duplicate identical rows. The
HTTP routes still require auth via ``require_user_id`` (access gate), but
they read from ``SYSTEM_USER_ID`` regardless of who requested.

Lifecycle pattern mirrors :class:`HyperliquidAddressBootstrap`:
named ``asyncio.Task`` instances tracked in ``self._tasks``, ``stop()`` flips
``_stopping`` and cancels with ``contextlib.suppress(asyncio.CancelledError)``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from time import monotonic
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.observability.metrics import (
    liq_scheduler_latency_seconds,
    liq_scheduler_runs_total,
)
from app.liquidation.models import TimeframeLiteral
from app.liquidation.providers._hyperliquid_client import HyperliquidClient
from app.liquidation.providers.derived import DerivedLiquidationProvider
from app.liquidation.providers.hyperliquid import HyperliquidLiquidationProvider
from app.liquidation.repo import LiquidationRepo
from app.liquidation.service import HeatmapService

LOG = logging.getLogger(__name__)

# Fixed sentinel for "system-owned" rows. Scheduler-generated snapshots are
# market data, not user data — they're written under this id and read back
# under it regardless of the authenticated user on the HTTP routes.
SYSTEM_USER_ID: Final[str] = "00000000-0000-0000-0000-000000000000"

# Cadence. Compromise between rightmost-edge freshness (operator sees live
# updates) and backend load (4 symbols x 3 timeframes = 12 service calls
# per cycle; at 120s that's 6 calls/min, easily absorbed).
SCHEDULER_INTERVAL_S: Final[int] = 120
JITTER_S: Final[int] = 15

SCHEDULER_TIMEFRAMES: Final[tuple[TimeframeLiteral, ...]] = ("1h", "4h", "1d")

# Default exchange whose latest 1m candle drives `current_price`. The
# liquidation engine itself is exchange-agnostic — the price just sets the
# magnet-zone window. Override per env if the project ever switches venues.
PRICE_EXCHANGE: Final[str] = "binance_usdm"


class LiquidationSnapshotScheduler:
    """Periodically snapshots ``WATCH_SYMBOLS x SCHEDULER_TIMEFRAMES``."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        hl_client: HyperliquidClient,
        watch_symbols: list[str],
    ) -> None:
        self._session_factory = session_factory
        self._hl_client = hl_client
        self._symbols = list(watch_symbols)
        self._tasks: list[asyncio.Task[None]] = []
        self._stopping = False

    async def start(self) -> None:
        if not self._symbols:
            LOG.info("liq_scheduler_skip_no_symbols")
            return
        self._tasks = [
            asyncio.create_task(self._loop(), name="liq_scheduler"),
        ]
        LOG.info(
            "liq_scheduler_started",
            extra={
                "symbols": self._symbols,
                "timeframes": list(SCHEDULER_TIMEFRAMES),
                "interval_s": SCHEDULER_INTERVAL_S,
            },
        )

    async def stop(self) -> None:
        self._stopping = True
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self._tasks.clear()

    async def _loop(self) -> None:
        # Small initial delay so we don't dogpile other lifespan startups
        # (LiveIngestion, AlertsRuntime, etc).
        await asyncio.sleep(random.uniform(2, 10))
        while not self._stopping:
            for symbol in self._symbols:
                for tf in SCHEDULER_TIMEFRAMES:
                    if self._stopping:
                        return
                    await self._snapshot_once(symbol, tf)
                    # Per-pair jitter spreads load across the interval —
                    # 12 pairs * ~7.5s avg jitter ≈ 90s of staggered work
                    # inside the 120s window.
                    await asyncio.sleep(random.uniform(0, JITTER_S))
            await asyncio.sleep(SCHEDULER_INTERVAL_S)

    async def _snapshot_once(
        self, symbol: str, timeframe: TimeframeLiteral
    ) -> None:
        start = monotonic()
        try:
            current_price = await self._fetch_current_price(symbol)
        except Exception:
            LOG.exception(
                "liq_scheduler_price_fetch_failed",
                extra={"symbol": symbol, "tf": timeframe},
            )
            liq_scheduler_runs_total.labels(
                symbol=symbol, timeframe=timeframe, outcome="no_price"
            ).inc()
            return
        if current_price is None or current_price <= 0:
            liq_scheduler_runs_total.labels(
                symbol=symbol, timeframe=timeframe, outcome="no_price"
            ).inc()
            return

        try:
            async with self._session_factory() as session:
                repo = LiquidationRepo(
                    session=session, user_id=SYSTEM_USER_ID
                )
                providers = [
                    DerivedLiquidationProvider(self._session_factory),
                    HyperliquidLiquidationProvider(
                        self._session_factory, self._hl_client
                    ),
                ]
                service = HeatmapService(providers=providers, repo=repo)
                snapshot = await service.get_snapshot(
                    symbol=symbol,
                    timeframe=timeframe,
                    current_price=current_price,
                )
        except Exception:
            LOG.exception(
                "liq_scheduler_snapshot_failed",
                extra={"symbol": symbol, "tf": timeframe},
            )
            liq_scheduler_runs_total.labels(
                symbol=symbol, timeframe=timeframe, outcome="error"
            ).inc()
            return
        finally:
            liq_scheduler_latency_seconds.labels(
                symbol=symbol, timeframe=timeframe
            ).observe(monotonic() - start)

        outcome = "empty" if not snapshot.magnet_zones else "ok"
        liq_scheduler_runs_total.labels(
            symbol=symbol, timeframe=timeframe, outcome=outcome
        ).inc()
        LOG.debug(
            "liq_scheduler_snapshot",
            extra={
                "symbol": symbol,
                "tf": timeframe,
                "zones": len(snapshot.magnet_zones),
                "agreement": snapshot.sources_agreement,
                "outcome": outcome,
            },
        )

    async def _fetch_current_price(self, symbol: str) -> float | None:
        """Latest 1m candle close — best proxy (fresh within 60s) without
        making an external HTTP call. If the 1m feed is offline for some
        symbol the scheduler logs and skips that cycle."""
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    text(
                        """
                        SELECT close::float AS price
                        FROM ohlcv
                        WHERE exchange = :exchange
                          AND symbol = :symbol
                          AND timeframe = '1m'
                        ORDER BY ts DESC
                        LIMIT 1
                        """
                    ),
                    {"exchange": PRICE_EXCHANGE, "symbol": symbol},
                )
            ).mappings().one_or_none()
        return float(row["price"]) if row else None
