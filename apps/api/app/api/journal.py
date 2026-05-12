"""REST endpoints para el journal de trades + setups con lifecycle.

Legacy:
- GET /journal/trades — lista filtrable (compat con `/research/journal`).
- GET /journal/trades/{id} — detail.

Nuevo (PR1):
- GET /journal/setups — setups propuestos por el agente con status.
- GET /journal/setups/{id} — detail con setup_events timeline.
- POST /journal/setups/{id}/cancel — soft-cancel de un setup pending.
- GET /strategies/winrate — agregados por setup_tag (closed only).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_user_id
from app.db import session_dependency
from app.storage.factor_stats_repo import get_holdout_performance_summary
from app.storage.post_mortem_repo import (
    get_post_mortem_by_trade_id,
    list_post_mortems,
)
from app.storage.review_repo import (
    get_review as get_review_row,
)
from app.storage.review_repo import (
    list_reviews_for_setup,
)
from app.storage.setup_repo import (
    cancel_setup,
    count_setups_by_status,
    get_setup_with_events,
    list_setups,
    winrate_by_setup_tag,
)

router = APIRouter()

JournalMode = Literal["paper", "live", "manual_log", "csv_import"]
SetupStatusLit = Literal["pending", "active", "closed", "cancelled"]


class JournalTradeListRow(BaseModel):
    id: str
    trade_ts: datetime
    symbol: str
    timeframe: str
    mode: str
    side: str
    entry_px: float
    exit_px: float | None
    size: float
    r_multiple: float | None
    setup_tag: str
    regime: str
    mistakes: str | None  # may be long; UI truncates


class JournalTradeDetail(JournalTradeListRow):
    summary_text: str
    summary_hash: str
    embedding_version: int
    news_24h: dict[str, Any]
    features: dict[str, Any]


@router.get("/journal/trades", response_model=list[JournalTradeListRow], tags=["research"])
async def list_journal_trades(
    session: Annotated[AsyncSession, Depends(session_dependency)],
    user_id: Annotated[str, Depends(require_user_id)],
    mode: Annotated[JournalMode | None, Query()] = None,
    regime: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[JournalTradeListRow]:
    sql = """
        SELECT id::text, trade_ts, symbol, timeframe, mode, side,
               entry_px, exit_px, size, r_multiple, setup_tag, regime, mistakes
        FROM journal_trades
        WHERE user_id = :uid
    """
    params: dict[str, Any] = {"uid": user_id, "lim": limit, "off": offset}
    if mode is not None:
        sql += " AND mode = :mode"
        params["mode"] = mode
    if regime is not None:
        sql += " AND regime = :regime"
        params["regime"] = regime
    sql += " ORDER BY trade_ts DESC LIMIT :lim OFFSET :off"
    rows = (await session.execute(text(sql), params)).mappings().all()
    return [JournalTradeListRow(**dict(r)) for r in rows]


@router.get(
    "/journal/trades/{trade_id}", response_model=JournalTradeDetail, tags=["research"]
)
async def get_journal_trade(
    trade_id: str,
    session: Annotated[AsyncSession, Depends(session_dependency)],
    user_id: Annotated[str, Depends(require_user_id)],
) -> JournalTradeDetail:
    row = (
        await session.execute(
            text(
                """
                SELECT id::text, trade_ts, symbol, timeframe, mode, side,
                       entry_px, exit_px, size, r_multiple, setup_tag, regime, mistakes,
                       summary_text, summary_hash, embedding_version,
                       news_24h, features
                FROM journal_trades
                WHERE id = CAST(:tid AS uuid) AND user_id = :uid
                """
            ),
            {"tid": trade_id, "uid": user_id},
        )
    ).mappings().one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail=f"journal_trade {trade_id} not found")
    return JournalTradeDetail(**dict(row))


# ---------------------------------------------------------------------------
# Setups (lifecycle pending → active → closed)
# ---------------------------------------------------------------------------


class SetupTargetDTO(BaseModel):
    label: str
    price: float
    rationale: str | None = None
    hit_at: datetime | None = None


class InvalidationConditionDTO(BaseModel):
    """Mirror del Pydantic model en `app.agent.models.InvalidationCondition`.
    El `spec` se pasa raw para la UI (no se reparsea aquí; el RuleSpec ya fue
    validado por el agent al emitir el TradeIdea)."""

    spec: dict[str, Any]
    rationale: str
    citations: list[dict[str, Any]] = []


class SetupListRow(BaseModel):
    id: str
    user_id: str
    trade_ts: datetime
    symbol: str
    timeframe: str
    side: str
    status: str
    source: str
    entry_px: float
    stop_loss_px: float | None
    exit_px: float | None
    size: float
    r_multiple: float | None
    setup_tag: str
    regime: str
    confidence: str | None
    targets: list[SetupTargetDTO]
    invalidation_conditions: list[InvalidationConditionDTO] = []
    expires_at: datetime | None = None
    mistakes: str | None
    proposed_at: datetime | None
    entry_hit_at: datetime | None
    closed_at: datetime | None
    invalidated_at: datetime | None = None
    created_at: datetime


class SetupEventRow(BaseModel):
    id: str
    event: str
    candle_ts: datetime
    payload: dict[str, Any]
    created_at: datetime


class SetupDetail(SetupListRow):
    summary_text: str
    summary_es_full: str | None = None
    news_24h: dict[str, Any]
    features: dict[str, Any]
    mistakes: str | None
    expires_at_rationale: str | None = None
    expires_at_citations: list[dict[str, Any]] | None = None
    updated_at: datetime
    events: list[SetupEventRow]


class StatusCounts(BaseModel):
    pending: int
    active: int
    closed: int
    cancelled: int


class SetupListResponse(BaseModel):
    rows: list[SetupListRow]
    counts: StatusCounts


@router.get(
    "/journal/setups",
    response_model=SetupListResponse,
    tags=["journal"],
)
async def list_journal_setups(
    session: Annotated[AsyncSession, Depends(session_dependency)],
    user_id: Annotated[str, Depends(require_user_id)],
    status: Annotated[SetupStatusLit | None, Query()] = None,
    symbol: Annotated[str | None, Query()] = None,
    source: Annotated[str | None, Query()] = "agent_proposal",
    setup_tag: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SetupListResponse:
    rows_raw = await list_setups(
        session,
        user_id=user_id,
        status=status,
        symbol=symbol,
        source=source,
        setup_tag=setup_tag,
        limit=limit,
        offset=offset,
    )
    counts = await count_setups_by_status(
        session, user_id=user_id, symbol=symbol
    )
    return SetupListResponse(
        rows=[SetupListRow(**r) for r in rows_raw],
        counts=StatusCounts(**counts),
    )


@router.get(
    "/journal/setups/{trade_id}",
    response_model=SetupDetail,
    tags=["journal"],
)
async def get_journal_setup(
    trade_id: str,
    session: Annotated[AsyncSession, Depends(session_dependency)],
    user_id: Annotated[str, Depends(require_user_id)],
) -> SetupDetail:
    row = await get_setup_with_events(
        session, user_id=user_id, trade_id=trade_id
    )
    if not row:
        raise HTTPException(
            status_code=404, detail=f"setup {trade_id} not found"
        )
    return SetupDetail(**row)


class TradeReviewRow(BaseModel):
    id: str
    trade_id: str
    user_id: str
    trigger_kind: str
    trigger_payload: dict[str, Any]
    current_state: str
    recommendation: str
    summary: str
    rationale: str
    citations: list[dict[str, Any]]
    price_at_review: float
    model_id: str
    usage_tokens: dict[str, Any] | None
    cost_usd: float | None
    prompt_version: str | None
    created_at: datetime


@router.get(
    "/journal/setups/{trade_id}/reviews",
    response_model=list[TradeReviewRow],
    tags=["journal"],
)
async def list_setup_reviews_endpoint(
    trade_id: str,
    session: Annotated[AsyncSession, Depends(session_dependency)],
    user_id: Annotated[str, Depends(require_user_id)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[TradeReviewRow]:
    rows = await list_reviews_for_setup(
        session, trade_id=trade_id, user_id=user_id, limit=limit
    )
    return [TradeReviewRow(**r) for r in rows]


@router.get(
    "/journal/reviews/{review_id}",
    response_model=TradeReviewRow,
    tags=["journal"],
)
async def get_journal_review(
    review_id: str,
    session: Annotated[AsyncSession, Depends(session_dependency)],
    user_id: Annotated[str, Depends(require_user_id)],
) -> TradeReviewRow:
    row = await get_review_row(session, review_id=review_id, user_id=user_id)
    if not row:
        raise HTTPException(
            status_code=404, detail=f"review {review_id} not found"
        )
    return TradeReviewRow(**row)


@router.post(
    "/journal/setups/{trade_id}/cancel",
    response_model=dict,
    tags=["journal"],
)
async def cancel_journal_setup(
    trade_id: str,
    session: Annotated[AsyncSession, Depends(session_dependency)],
    user_id: Annotated[str, Depends(require_user_id)],
) -> dict[str, Any]:
    ok = await cancel_setup(session, user_id=user_id, trade_id=trade_id)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail=(
                "setup not cancellable (must exist, belong to user, "
                "and have status='pending')"
            ),
        )
    return {"status": "cancelled"}


# ---------------------------------------------------------------------------
# Strategies / winrate (PR1 backend, PR2 frontend)
# ---------------------------------------------------------------------------


class StrategyWinrateRow(BaseModel):
    setup_tag: str
    n_closed: int
    n_wins: int
    win_rate_pct: float | None
    avg_r: float | None
    last_closed_at: datetime | None


@router.get(
    "/strategies/winrate",
    response_model=list[StrategyWinrateRow],
    tags=["research", "journal"],
)
async def list_strategy_winrate(
    session: Annotated[AsyncSession, Depends(session_dependency)],
    user_id: Annotated[str, Depends(require_user_id)],
    min_n: Annotated[int, Query(ge=1, le=100)] = 1,
) -> list[StrategyWinrateRow]:
    rows = await winrate_by_setup_tag(session, user_id=user_id, min_n=min_n)
    return [StrategyWinrateRow(**r) for r in rows]


# ---------------------------------------------------------------------------
# F5.5 — post-mortems endpoints
# ---------------------------------------------------------------------------


class PostMortemResponse(BaseModel):
    id: str
    trade_id: str
    user_id: str
    outcome: str
    r_multiple: float
    exit_reason: str
    verdict: str
    confidence_calibration: str
    factor_verdicts: dict[str, Any]
    lesson_es: str
    summary_es: str
    counterfactual_es: str | None
    entry_vs_exit_delta: dict[str, Any] | None
    citations: list[dict[str, Any]]
    model_id: str
    usage_tokens: dict[str, Any] | None
    cost_usd: float | None
    prompt_version: str | None
    created_at: datetime


@router.get(
    "/journal/setups/{trade_id}/post-mortem",
    response_model=PostMortemResponse,
    tags=["journal"],
)
async def get_setup_post_mortem(
    trade_id: str,
    session: Annotated[AsyncSession, Depends(session_dependency)],
    user_id: Annotated[str, Depends(require_user_id)],
) -> PostMortemResponse:
    """Post-mortem único asociado a un trade cerrado. 404 si todavía no
    se ejecutó (feature flag off, dispatcher pendiente o ID inválido)."""
    row = await get_post_mortem_by_trade_id(
        session, trade_id=trade_id, user_id=user_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="post_mortem_not_found")
    return PostMortemResponse(**row)


@router.get(
    "/journal/post-mortems",
    response_model=list[PostMortemResponse],
    tags=["journal"],
)
async def list_setup_post_mortems(
    session: Annotated[AsyncSession, Depends(session_dependency)],
    user_id: Annotated[str, Depends(require_user_id)],
    outcome: Annotated[
        Literal["win", "loss", "breakeven", "partial_win"] | None,
        Query(description="Filtrar por outcome bucket."),
    ] = None,
    verdict: Annotated[
        Literal["thesis_held", "thesis_broken", "execution_error", "noise"] | None,
        Query(description="Filtrar por verdict del agente."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[PostMortemResponse]:
    rows = await list_post_mortems(
        session, user_id=user_id, outcome=outcome, verdict=verdict, limit=limit
    )
    return [PostMortemResponse(**r) for r in rows]


# ---------------------------------------------------------------------------
# F5.5 — holdout performance + MFE/MAE stats (anti-overfit monitoring)
# ---------------------------------------------------------------------------


class HoldoutBucket(BaseModel):
    n: int
    win_rate: float | None
    avg_r: float | None


class HoldoutPerformanceResponse(BaseModel):
    in_sample: HoldoutBucket
    holdout: HoldoutBucket
    delta_pp: float | None
    drift_warning: bool


@router.get(
    "/journal/holdout-performance",
    response_model=HoldoutPerformanceResponse,
    tags=["journal", "monitoring"],
)
async def get_holdout_performance(
    session: Annotated[AsyncSession, Depends(session_dependency)],
    user_id: Annotated[str, Depends(require_user_id)],
) -> HoldoutPerformanceResponse:
    """Comparativa in-sample vs holdout para detectar overfit del feedback
    loop. Si `|delta_pp| > 8` con muestras razonables (>= 20 en cada lado),
    el sistema está aprendiendo demasiado de su propio histórico."""
    summary = await get_holdout_performance_summary(session, user_id=user_id)
    in_sample = summary["in_sample"]
    holdout = summary["holdout"]
    delta_pp = summary["delta_pp"]
    drift_warning = bool(
        delta_pp is not None
        and abs(delta_pp) > 8.0
        and in_sample.get("n", 0) >= 20
        and holdout.get("n", 0) >= 20
    )
    return HoldoutPerformanceResponse(
        in_sample=HoldoutBucket(**in_sample),
        holdout=HoldoutBucket(**holdout),
        delta_pp=delta_pp,
        drift_warning=drift_warning,
    )


class MfeMaeStats(BaseModel):
    n: int
    mfe_p25: float | None
    mfe_p50: float | None
    mfe_p75: float | None
    mae_p25: float | None
    mae_p50: float | None
    mae_p75: float | None
    exit_efficiency_p50: float | None


@router.get(
    "/journal/mfe-mae-stats",
    response_model=MfeMaeStats,
    tags=["journal", "monitoring"],
)
async def get_mfe_mae_stats(
    session: Annotated[AsyncSession, Depends(session_dependency)],
    user_id: Annotated[str, Depends(require_user_id)],
    lookback_days: Annotated[int, Query(ge=7, le=730)] = 180,
) -> MfeMaeStats:
    """Percentiles de MFE/MAE en R-units. p75 alto de MFE con r_multiple
    bajo = exits prematuros (sales antes de capturar el máximo). p25 muy
    bajo de MAE = SL demasiado ajustado (wicks te están barriendo)."""
    row = (
        await session.execute(
            text(
                """
                SELECT
                    COUNT(*) AS n,
                    percentile_cont(0.25) WITHIN GROUP (
                        ORDER BY (mfe_mae->>'mfe_r')::numeric
                    ) AS mfe_p25,
                    percentile_cont(0.50) WITHIN GROUP (
                        ORDER BY (mfe_mae->>'mfe_r')::numeric
                    ) AS mfe_p50,
                    percentile_cont(0.75) WITHIN GROUP (
                        ORDER BY (mfe_mae->>'mfe_r')::numeric
                    ) AS mfe_p75,
                    percentile_cont(0.25) WITHIN GROUP (
                        ORDER BY (mfe_mae->>'mae_r')::numeric
                    ) AS mae_p25,
                    percentile_cont(0.50) WITHIN GROUP (
                        ORDER BY (mfe_mae->>'mae_r')::numeric
                    ) AS mae_p50,
                    percentile_cont(0.75) WITHIN GROUP (
                        ORDER BY (mfe_mae->>'mae_r')::numeric
                    ) AS mae_p75,
                    percentile_cont(0.50) WITHIN GROUP (
                        ORDER BY (mfe_mae->>'exit_efficiency_pct')::numeric
                    ) FILTER (
                        WHERE (mfe_mae->>'exit_efficiency_pct') IS NOT NULL
                    ) AS efficiency_p50
                FROM journal_trades
                WHERE user_id = :uid
                  AND status = 'closed'
                  AND source = 'agent_proposal'
                  AND mfe_mae IS NOT NULL
                  AND closed_at >= now() - make_interval(days => :lb)
                """
            ),
            {"uid": user_id, "lb": lookback_days},
        )
    ).mappings().one()
    return MfeMaeStats(
        n=int(row["n"] or 0),
        mfe_p25=float(row["mfe_p25"]) if row["mfe_p25"] is not None else None,
        mfe_p50=float(row["mfe_p50"]) if row["mfe_p50"] is not None else None,
        mfe_p75=float(row["mfe_p75"]) if row["mfe_p75"] is not None else None,
        mae_p25=float(row["mae_p25"]) if row["mae_p25"] is not None else None,
        mae_p50=float(row["mae_p50"]) if row["mae_p50"] is not None else None,
        mae_p75=float(row["mae_p75"]) if row["mae_p75"] is not None else None,
        exit_efficiency_p50=(
            float(row["efficiency_p50"])
            if row["efficiency_p50"] is not None
            else None
        ),
    )
