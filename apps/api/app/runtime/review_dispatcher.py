"""Dispatcher que invoca al `review_agent` desde el SetupRuntime.

Punto único de entrada para todas las reviews automáticas. Maneja:
- Cooldown atómico (claim_review_slot — UPDATE condicional).
- Re-check del status del setup (puede haber cerrado entre dispatch y aquí).
- Construcción del user prompt con el snapshot del trade y el trigger.
- Invocación de `agent.run()` con AgentDeps construido programáticamente.
- Persistencia (insert_review + setup_events + bump cooldown).
- Publicación al canal Valkey `reviews:user:{user_id}` para WS push (F4).
- Throttling global con semáforo (REVIEW_CONCURRENCY).
- Extracción de usage + cálculo de cost_usd (F6).
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import text

from app.agent.deps import AgentDeps
from app.agent.models import TradeReview, TriggerKind
from app.agent.review_agent import REVIEW_MODEL_ID, get_review_agent
from app.agent.review_system_prompt import REVIEW_SYSTEM_PROMPT_VERSION
from app.broadcasting.pubsub import publish_json, reviews_channel
from app.config import get_settings
from app.db import session_scope
from app.storage.review_repo import (
    claim_review_slot,
    compute_next_review_at,
    get_last_reviews,
    insert_review,
)
from app.storage.setup_repo import OpenSetupRow

log = structlog.get_logger(__name__)

# Global semaphore — lazily created to avoid binding to a different event loop.
_concurrency_lock: asyncio.Lock | None = None
_concurrency_sem: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _concurrency_sem
    if _concurrency_sem is None:
        _concurrency_sem = asyncio.Semaphore(get_settings().review_concurrency)
    return _concurrency_sem


async def maybe_run_review(
    *,
    setup: OpenSetupRow,
    trigger_kind: TriggerKind,
    trigger_payload: dict[str, Any],
    current_price: float,
    candle_ts: datetime,
) -> str | None:
    """Fire-and-forget entry point. Devuelve el review_id si se emitió, None
    si cooldown/cap/status-changed bloqueó la ejecución."""
    settings = get_settings()
    try:
        return await _maybe_run_review_inner(
            setup=setup,
            trigger_kind=trigger_kind,
            trigger_payload=trigger_payload,
            current_price=current_price,
            candle_ts=candle_ts,
            settings=settings,
        )
    except Exception as exc:
        log.warning(
            "review.failed",
            setup_id=setup.id,
            trigger_kind=trigger_kind,
            error=type(exc).__name__,
            message=str(exc)[:200],
        )
        return None


async def _maybe_run_review_inner(
    *,
    setup: OpenSetupRow,
    trigger_kind: TriggerKind,
    trigger_payload: dict[str, Any],
    current_price: float,
    candle_ts: datetime,
    settings: Any,
) -> str | None:
    # 1a) Discriminate cap_reached antes del claim para distinguir el log:
    #     una read-only query barata; el claim hace el UPDATE atómico real.
    async with session_scope() as session:
        cur_count = (
            await session.execute(
                text(
                    "SELECT review_count FROM journal_trades "
                    "WHERE id = CAST(:tid AS uuid)"
                ),
                {"tid": setup.id},
            )
        ).scalar_one_or_none()
    if cur_count is not None and int(cur_count) >= settings.review_max_per_setup:
        log.warning(
            "review.cap_reached",
            setup_id=setup.id,
            trigger_kind=trigger_kind,
            review_count=int(cur_count),
            cap=settings.review_max_per_setup,
        )
        return None

    # 1b) Atomic cooldown + cap claim. UPDATE condicional bumpea
    #     last_review_attempt_at. Si rebota → otro proceso reclamó o cap
    #     (1a normalmente captura el cap antes — la guard aquí es defensa
    #     en profundidad para una race entre la read y el claim).
    async with session_scope() as session:
        claimed = await claim_review_slot(
            session,
            trade_id=setup.id,
            cooldown_minutes=settings.review_cooldown_min_minutes,
            max_reviews=settings.review_max_per_setup,
        )
    if not claimed:
        log.debug(
            "review.cooldown_blocked",
            setup_id=setup.id,
            trigger_kind=trigger_kind,
        )
        return None

    # 2) Throttle global con semáforo (bound concurrent agent calls).
    sem = _get_semaphore()
    async with sem:
        # 3) Re-verify status DESPUÉS del cooldown claim — el setup pudo
        #    haber cerrado (SL hit, todos TPs) mientras esperábamos slot.
        status_ok = await _setup_is_open_for_review(setup.id)
        if not status_ok:
            log.debug(
                "review.setup_no_longer_active",
                setup_id=setup.id,
                trigger_kind=trigger_kind,
            )
            return None

        # 4) Historic context: las últimas 3 reviews.
        async with session_scope() as session:
            prior_reviews = await get_last_reviews(
                session, trade_id=setup.id, limit=3
            )

        # 5) Build user prompt + deps + invoke.
        prompt = _build_review_user_prompt(
            setup=setup,
            trigger_kind=trigger_kind,
            trigger_payload=trigger_payload,
            current_price=current_price,
            candle_ts=candle_ts,
            prior_reviews=prior_reviews,
        )
        deps = AgentDeps(
            session_factory=session_scope,
            log=log,
            user_id=setup.user_id,
        )

        log.info(
            "review.dispatched",
            setup_id=setup.id,
            user_id=setup.user_id,
            symbol=setup.symbol,
            timeframe=setup.timeframe,
            trigger_kind=trigger_kind,
        )
        started = time.perf_counter()
        result = await get_review_agent().run(prompt, deps=deps)
        duration_ms = int((time.perf_counter() - started) * 1000)

        review: TradeReview = result.output

    # 6) Extract usage + cost.
    usage_tokens, cost_usd = _extract_usage_and_cost(result, settings)

    # 7) Compute next time-based review.
    now_utc = datetime.now(tz=UTC)
    next_review_at = compute_next_review_at(
        entry_hit_at=setup.entry_hit_at,
        now=now_utc,
        offsets_hours=settings.review_time_offsets_list,
    )

    # 8) Persist (review + event + cooldown bumps).
    async with session_scope() as session:
        review_id = await insert_review(
            session,
            trade_id=setup.id,
            user_id=setup.user_id,
            trigger_kind=trigger_kind,
            trigger_payload=trigger_payload,
            review=review,
            price_at_review=current_price,
            model_id=REVIEW_MODEL_ID,
            usage_tokens=usage_tokens,
            cost_usd=cost_usd,
            prompt_version=REVIEW_SYSTEM_PROMPT_VERSION,
            next_review_at=next_review_at,
        )

    # 9) Push al frontend (best-effort; falla silenciosa si Valkey está down).
    try:
        await publish_json(
            reviews_channel(setup.user_id),
            {
                "type": "trade_review",
                "review_id": review_id,
                "setup_id": setup.id,
                "symbol": setup.symbol,
                "timeframe": setup.timeframe,
                "side": setup.side,
                "trigger_kind": trigger_kind,
                "trigger_payload": trigger_payload,
                "current_state": review.current_state,
                "recommendation": review.recommendation,
                "summary": review.summary,
                "rationale": review.rationale,
                "citations": [c.model_dump(mode="json") for c in review.citations],
                "price_at_review": current_price,
                "created_at": now_utc.isoformat(),
            },
        )
    except Exception as exc:
        log.warning(
            "review.publish_failed",
            review_id=review_id,
            error=type(exc).__name__,
        )

    log.info(
        "review.completed",
        review_id=review_id,
        setup_id=setup.id,
        trigger_kind=trigger_kind,
        current_state=review.current_state,
        recommendation=review.recommendation,
        duration_ms=duration_ms,
        input_tokens=usage_tokens.get("input") if usage_tokens else None,
        output_tokens=usage_tokens.get("output") if usage_tokens else None,
        cache_read_tokens=usage_tokens.get("cache_read") if usage_tokens else None,
        cost_usd=cost_usd,
    )
    return review_id


async def _setup_is_open_for_review(trade_id: str) -> bool:
    """Re-check: el setup debe seguir en pending/active. Si transitó a
    closed/cancelled mientras esperábamos slot, abort."""
    async with session_scope() as session:
        row = (
            await session.execute(
                text(
                    "SELECT status FROM journal_trades "
                    "WHERE id = CAST(:tid AS uuid)"
                ),
                {"tid": trade_id},
            )
        ).one_or_none()
    if row is None:
        return False
    return row[0] in ("pending", "active")


def _build_review_user_prompt(
    *,
    setup: OpenSetupRow,
    trigger_kind: TriggerKind,
    trigger_payload: dict[str, Any],
    current_price: float,
    candle_ts: datetime,
    prior_reviews: list[dict[str, Any]],
) -> str:
    """Construye el user message que se inyecta en agent.run(). Todo el
    contexto per-request va aquí (NUNCA en el system prompt — rompería el
    Anthropic cache).

    Estructura:
    1. **Setup** — precios fijos (entry/SL/targets) + metadata temporal.
    2. **Tesis original** — la lectura del agente principal al proponerlo:
       régimen, confidence, summary_es completo, confluences multi-TF y
       scenarios planteados. Es el "porqué" que el review debe juzgar.
    3. **Trigger** de esta review.
    4. **Estado actual** — precio + R unrealized.
    5. **Reviews previas** — últimas 3 reviews compactas (state/reco/summary).
    6. **Cierre** — instrucción al agente.
    """
    targets_block = (
        "\n".join(
            f"  - {t.get('label', '?')}: {t.get('price', '?')}"
            f" {'(hit_at=' + str(t.get('hit_at')) + ')' if t.get('hit_at') else '(pending)'}"
            for t in setup.targets
        )
        if setup.targets
        else "  (sin targets registrados)"
    )

    entry_hit_iso = (
        setup.entry_hit_at.isoformat()
        if setup.entry_hit_at is not None
        else "(aún pending)"
    )
    if setup.entry_hit_at is not None:
        delta = datetime.now(tz=UTC) - setup.entry_hit_at
        hours_since_entry = round(delta.total_seconds() / 3600, 1)
        hours_since_entry_str = f"hace {hours_since_entry}h"
    else:
        hours_since_entry_str = "—"

    pct_from_entry = (
        (current_price - setup.entry_px) / setup.entry_px * 100.0
        if setup.entry_px
        else 0.0
    )

    if setup.stop_loss_px is not None and setup.entry_px:
        risk = abs(setup.entry_px - setup.stop_loss_px)
        if setup.side == "long":
            r_unrealized = (current_price - setup.entry_px) / risk if risk else 0.0
        else:
            r_unrealized = (setup.entry_px - current_price) / risk if risk else 0.0
        r_unrealized_str = f"{r_unrealized:+.2f}R"
    else:
        r_unrealized_str = "—"

    prior_block = (
        "\n".join(
            f"  - [{r.get('created_at')}] {r.get('trigger_kind')}: "
            f"{r.get('current_state')}/{r.get('recommendation')} — "
            f"{(r.get('summary') or '')[:140]}"
            for r in prior_reviews
        )
        if prior_reviews
        else "  (sin reviews previas)"
    )

    thesis_block = _format_thesis_block(setup)

    return (
        f"Trade en revisión:\n"
        f"- Symbol: {setup.symbol} ({setup.timeframe})\n"
        f"- Side: {setup.side}\n"
        f"- Entry: {setup.entry_px} (activado {entry_hit_iso}, {hours_since_entry_str})\n"
        f"- SL: {setup.stop_loss_px}\n"
        f"- Targets:\n{targets_block}\n"
        f"\n"
        f"{thesis_block}"
        f"\n"
        f"Trigger de esta review: {trigger_kind} — {trigger_payload}\n"
        f"\n"
        f"Estado actual:\n"
        f"- Precio: {current_price} ({pct_from_entry:+.2f}% desde entry, {r_unrealized_str})\n"
        f"- Now (UTC): {candle_ts.astimezone(UTC).isoformat()}\n"
        f"\n"
        f"Reviews previas (last 3):\n{prior_block}\n"
        f"\n"
        f"Tu trabajo: validar si la TESIS ORIGINAL sigue intacta a la luz de "
        f"los datos actuales. Llama las tools que necesites para verificar "
        f"(estructura, indicadores, volumen, derivados, correlación). Emite "
        f"un TradeReview siguiendo el decision tree de tu system prompt. NO "
        f"recalcules entry ni stop_loss — son fijos del setup."
    )


def _format_thesis_block(setup: OpenSetupRow) -> str:
    """Renders la tesis original (regime, confidence, summary_es, confluences,
    scenarios) como un bloque legible. Si los campos son None/vacíos (setup
    pre-migration 010), devuelve un bloque mínimo sin contaminar el prompt.
    """
    if not setup.summary_es_full and not setup.confluences and not setup.scenarios:
        return ""

    lines: list[str] = ["Tesis original del setup (al momento de proponerlo):"]
    meta_parts: list[str] = []
    if setup.regime:
        meta_parts.append(f"régimen={setup.regime}")
    if setup.confidence:
        meta_parts.append(f"confidence={setup.confidence}")
    if meta_parts:
        lines.append("- " + " · ".join(meta_parts))

    if setup.summary_es_full:
        lines.append(f"- Resumen del agente:\n  {setup.summary_es_full}")

    if setup.confluences:
        lines.append("- Confluencias multi-TF:")
        for c in setup.confluences:
            tf = c.get("timeframe", "?")
            bias = c.get("bias", "?")
            narrative = (c.get("narrative") or "").strip()
            lines.append(f"  - [{tf}] {bias}: {narrative}")

    if setup.scenarios:
        lines.append("- Escenarios planteados:")
        for s in setup.scenarios:
            label = s.get("label", "?")
            prob = s.get("probability_pct")
            desc = (s.get("description") or "").strip()
            prob_str = f"{prob}%" if prob is not None else "?"
            lines.append(f"  - {label} ({prob_str}): {desc}")

    return "\n".join(lines) + "\n"


def _extract_usage_and_cost(
    result: Any, settings: Any
) -> tuple[dict[str, Any] | None, float | None]:
    """Best-effort extraction de tokens + cost_usd estimate. Tolerante a
    cambios de API en pydantic-ai (los campos exactos varían entre versiones).
    """
    try:
        usage = result.usage()
    except Exception:
        return None, None

    def _get(name: str) -> int:
        v = getattr(usage, name, 0)
        try:
            return int(v) if v is not None else 0
        except (TypeError, ValueError):
            return 0

    input_t = _get("input_tokens") or _get("request_tokens")
    output_t = _get("output_tokens") or _get("response_tokens")
    cache_read = _get("cache_read_input_tokens") or _get("cache_read_tokens")
    cache_write = _get("cache_write_input_tokens") or _get("cache_creation_tokens")

    usage_tokens: dict[str, Any] = {
        "input": input_t,
        "output": output_t,
        "cache_read": cache_read,
        "cache_create": cache_write,
        "total": input_t + output_t,
    }

    in_per_m = settings.review_price_input_per_m_usd
    out_per_m = settings.review_price_output_per_m_usd
    cache_per_m = settings.review_price_cache_read_per_m_usd

    chargeable_input = max(input_t - cache_read, 0)
    cost = (
        chargeable_input * in_per_m / 1_000_000
        + cache_read * cache_per_m / 1_000_000
        + output_t * out_per_m / 1_000_000
    )
    return usage_tokens, round(cost, 6)
