"""POST /chat — Vercel AI SDK Data Stream Protocol endpoint.

Wires Pydantic AI's `VercelAIAdapter` to FastAPI; the adapter handles the SSE
encoding (text-delta, reasoning-delta, tool-input-available, tool-output-available,
finish, error) so we don't have to. Auth: the request's BetterAuth session cookie
resolves to a user_id which flows into AgentDeps so every tool can scope writes
to the authenticated user.

F5.5 — Auto-inject `<historic_stats>` preamble:
    Antes de pasar el request al adapter, leemos el body, parseamos los
    `messages` (AI SDK shape), localizamos el último user message, y le
    anteponemos un bloque compacto con hit-rates Bayesian por factor
    histórico del usuario. Cero overhead de ModelRetry: el agente VE las
    stats en cada turno sin necesidad de llamar la tool.

    El feedback loop CIERRA aquí (no en el system prompt — eso invalidaría
    el cache de Anthropic). ~50 tokens extra/turn = ~$0.0002/turno.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Request, Response
from pydantic_ai.ui.vercel_ai import VercelAIAdapter

from app.agent.agent import get_agent
from app.agent.deps import AgentDeps
from app.agent.tools.confluence import compute_score_components
from app.backtest.factor_stats_repo import get_top_factors_for_preamble
from app.core.auth import require_user_id
from app.core.config import get_settings
from app.core.db import session_scope
from app.storage.post_mortem_repo import (
    RecurringLesson,
    get_recurring_lessons_for_preamble,
)

router = APIRouter()
log = structlog.get_logger("api.chat")


@router.post("/chat", tags=["agent"])
async def chat(
    request: Request,
    user_id: Annotated[str, Depends(require_user_id)],
) -> Response:
    settings = get_settings()
    deps = AgentDeps(
        session_factory=session_scope,
        log=structlog.get_logger("agent.run"),
        user_id=user_id,
    )

    # F5.5 — preamble auto-inject. Feature flag default OFF; cuando se
    # activa, prepend el bloque al último user message.
    if getattr(settings, "historic_stats_preamble_enabled", False):
        try:
            await _inject_historic_stats_preamble(
                request=request,
                user_id=user_id,
                exchange=getattr(settings, "exchange", "binance_usdm"),
                preamble_symbol=_pick_preamble_symbol(request, settings),
            )
        except Exception as exc:
            # No bloquear el chat por un fallo de preamble. El agente sigue
            # funcionando sin las stats — solo perdemos el sesgo del feedback.
            log.warning(
                "chat.preamble_inject_failed",
                error=type(exc).__name__,
                message=str(exc)[:200],
            )

    log.info("chat.request.start", user_id=user_id)
    response = await VercelAIAdapter.dispatch_request(
        request,
        agent=get_agent(),
        deps=deps,
    )
    # Anti-buffering en proxies/edges (Railway-edge incluido). Sin esto algunos
    # proxies acumulan los chunks SSE hasta superar un buffer interno antes de
    # entregarlos, lo que se manifiesta como ERR_CONNECTION_RESET en el browser
    # cuando el agent tarda en emitir tokens (tool calls largas).
    response.headers["Cache-Control"] = "no-cache, no-transform"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


# ---------------------------------------------------------------------------
# Preamble injection
# ---------------------------------------------------------------------------


def _pick_preamble_symbol(request: Request, settings: Any) -> str:
    """Resuelve el símbolo para el cómputo del régimen actual. Preferencia:
    1) header `X-Active-Symbol` del frontend (zustand store).
    2) primer símbolo de WATCH_SYMBOLS.
    3) `BTCUSDT` como último recurso.
    """
    header_sym = request.headers.get("x-active-symbol") or request.headers.get("X-Active-Symbol")
    if header_sym:
        return header_sym.upper()
    watch = getattr(settings, "watch_symbol_list", None)
    if watch:
        return watch[0]
    return "BTCUSDT"


async def _inject_historic_stats_preamble(
    *,
    request: Request,
    user_id: str,
    exchange: str,
    preamble_symbol: str,
) -> None:
    """Lee el body del request, parsea los messages (AI SDK protocol),
    localiza el último user message y le antepone el bloque
    `<historic_stats>`. Reescribe `request._body` para que el adapter
    consuma el body modificado.

    Si el body no parsea o no encontramos el shape esperado, NO hacemos
    nada — la request original sigue a la siguiente capa intacta.
    """
    body_bytes = await request.body()
    if not body_bytes:
        return
    try:
        body = json.loads(body_bytes)
    except json.JSONDecodeError:
        return
    if not isinstance(body, dict):
        return

    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return

    # Localiza el último user message. AI SDK shape:
    # {"role": "user", "parts": [{"type": "text", "text": "..."}, ...]}
    # OR {"role": "user", "content": "..."} (legacy).
    last_user_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if isinstance(m, dict) and m.get("role") == "user":
            last_user_idx = i
            break
    if last_user_idx < 0:
        return

    # Compute current regime (best-effort).
    regime_label: str | None = None
    try:
        cmap = await compute_score_components(
            session_factory=session_scope,
            exchange=exchange,
            symbol=preamble_symbol,
            timeframes=["4h"],
        )
        regime_label = (
            "trending_up"
            if cmap.aggregate_bias == "bull"
            else "trending_down"
            if cmap.aggregate_bias == "bear"
            else "ranging"
        )
    except Exception:
        regime_label = None

    # Fetch top factors (regime-bucketed + global fallback) + recurring
    # lessons (A.3). All three queries share a single session to keep pool
    # pressure low.
    async with session_scope() as session:
        regime_factors = (
            await get_top_factors_for_preamble(
                session,
                user_id=user_id,
                regime_label=regime_label,
                lookback_days=180,
                limit=6,
                min_n=3,
            )
            if regime_label
            else []
        )
        global_factors = await get_top_factors_for_preamble(
            session,
            user_id=user_id,
            regime_label=None,
            lookback_days=180,
            limit=6,
            min_n=3,
        )
        recurring_lessons = await get_recurring_lessons_for_preamble(
            session,
            user_id=user_id,
            regime_label=regime_label,
            n_lookback=50,
            top_k=3,
            min_occurrences=2,
        )

    preamble = _format_preamble(
        regime_label=regime_label,
        regime_factors=regime_factors,
        global_factors=global_factors,
        recurring_lessons=recurring_lessons,
        symbol=preamble_symbol,
    )
    if not preamble:
        return

    # Inject into the last user message.
    msg = messages[last_user_idx]
    parts = msg.get("parts")
    if isinstance(parts, list) and parts:
        # AI SDK v5 shape: parts is a list of {"type": "text", "text": "..."}.
        # Prepend a new text part with the preamble.
        msg["parts"] = [{"type": "text", "text": preamble}, *parts]
    elif isinstance(msg.get("content"), str):
        msg["content"] = preamble + "\n\n" + msg["content"]
    elif isinstance(msg.get("content"), list):
        # Antrhopic-like content blocks.
        msg["content"] = [
            {"type": "text", "text": preamble},
            *msg["content"],
        ]
    else:
        # Shape desconocido — no toques.
        return

    body["messages"] = messages
    new_body = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    # Override Starlette's cached body so subsequent reads (by the adapter)
    # see the modified version. The Content-Length header is informational
    # for buffered requests — we override _body which is the source of truth.
    request._body = new_body  # type: ignore[attr-defined]

    log.info(
        "chat.preamble_injected",
        user_id=user_id,
        regime=regime_label,
        symbol=preamble_symbol,
        n_regime_factors=len(regime_factors),
        n_global_factors=len(global_factors),
        n_recurring_lessons=len(recurring_lessons),
        preamble_len=len(preamble),
    )


def _format_preamble(
    *,
    regime_label: str | None,
    regime_factors: list,
    global_factors: list,
    recurring_lessons: list[RecurringLesson],
    symbol: str,
) -> str:
    """Construye los bloques `<historic_stats>` (factor win-rates) y
    `<recurring_lessons>` (patrones de error recurrentes) para inyección.

    Ambos bloques son opcionales — si no hay data suficiente para uno, se
    omite. Si NINGUNO produce contenido, devolvemos string vacío y el caller
    no inyecta nada al user message.
    """
    blocks: list[str] = []

    stats_block = _format_historic_stats_block(
        regime_label=regime_label,
        regime_factors=regime_factors,
        global_factors=global_factors,
        symbol=symbol,
    )
    if stats_block:
        blocks.append(stats_block)

    lessons_block = _format_recurring_lessons_block(
        recurring_lessons=recurring_lessons,
        regime_label=regime_label,
    )
    if lessons_block:
        blocks.append(lessons_block)

    return "\n\n".join(blocks)


def _format_historic_stats_block(
    *,
    regime_label: str | None,
    regime_factors: list,
    global_factors: list,
    symbol: str,
) -> str:
    """Construye el bloque `<historic_stats>`.

    Formato (decisión = `wr_lcb`, no `wr_mean`):

        <historic_stats lookback="180d" regime="trending_up" symbol="BTCUSDT">
        Factores con WR_lcb >= 50% (los que sostienen ganancias):
          - ema_stack@1h: WR_lcb 58% · avg +0.42R · n=12
        Factores con WR_lcb < 30% (los que penalizan):
          - rsi@1h: WR_lcb 22% · avg -0.31R · n=9 ⚠
        </historic_stats>

    NOTA: usar `win_rate_lcb` (5th percentile del Beta-Binomial posterior).
    Sample pequeño → lcb cae mucho → el agente VE "sin evidencia" sin
    necesidad de flag artificial.
    """
    factors = regime_factors if regime_factors else global_factors
    if not factors:
        return ""

    strong = [f for f in factors if f.win_rate_lcb >= 0.50]
    weak = [f for f in factors if f.win_rate_lcb < 0.30]

    if not strong and not weak:
        return ""

    def _key(f: object) -> str:
        name = getattr(f, "factor_name", "?")
        tf = getattr(f, "factor_tf", None)
        return f"{name}@{tf}" if tf else name

    def _row(f: object) -> str:
        avg_r = getattr(f, "avg_r", None)
        avg_part = f"avg {avg_r:+.2f}R" if avg_r is not None else "avg —"
        return (
            f"  - {_key(f)}: WR_lcb {getattr(f, 'win_rate_lcb', 0.0):.0%} · "
            f"{avg_part} · n={getattr(f, 'n_trades', 0)}"
        )

    attrs = ['lookback="180d"', f'symbol="{symbol}"']
    if regime_label:
        attrs.append(f'regime="{regime_label}"')
        scope_note = (
            "(stats segmentadas por régimen actual; usa estos números, no "
            "tu intuición de mercado general)"
        )
    else:
        attrs.append('regime="any"')
        scope_note = "(stats globales; régimen no detectable ahora)"

    lines = [f"<historic_stats {' '.join(attrs)}>"]
    lines.append(f"Generated at {datetime.utcnow().isoformat()}Z. {scope_note}")
    if strong:
        lines.append("Factores fuertes (WR_lcb ≥ 50% — favorece confirmar tesis):")
        for f in strong:
            lines.append(_row(f))
    if weak:
        lines.append("Factores débiles (WR_lcb < 30% — exige justificación si los citas):")
        for f in weak:
            lines.append(_row(f) + " ⚠")
    lines.append("</historic_stats>")
    return "\n".join(lines)


def _format_recurring_lessons_block(
    *,
    recurring_lessons: list[RecurringLesson],
    regime_label: str | None,
) -> str:
    """Bloque `<recurring_lessons>` — patrones de error que se repiten en el
    historial reciente del usuario bajo el régimen actual.

    Formato:
        <recurring_lessons regime="trending_up" lookback="50_trades">
        Patrones de error que has repetido (clusters por similaridad léxica):
          - "En régimen ranging, ema_stack@1h por sí solo no basta..." (3 veces; BTCUSDT, SOLUSDT)
          - "Volume confirmation < 1.3× → entries weak..." (2 veces; ETHUSDT)
        </recurring_lessons>

    Si la lección es muy larga, se trunca a 200 chars con elipsis para
    mantener el preamble compacto.
    """
    if not recurring_lessons:
        return ""

    attrs = ['lookback="50_trades"']
    if regime_label:
        attrs.append(f'regime="{regime_label}"')
        scope_note = "(filtrado por régimen actual)"
    else:
        attrs.append('regime="any"')
        scope_note = "(régimen no detectable ahora; muestra global)"

    lines = [f"<recurring_lessons {' '.join(attrs)}>"]
    lines.append(
        f"Patrones que se repiten en tus thesis_broken {scope_note}. Lee "
        f"antes de proponer — si tu setup encaja con uno de estos, exige "
        f"evidencia adicional o cambia a no_trade."
    )
    for lesson in recurring_lessons:
        text = lesson.lesson_es.strip()
        if len(text) > 200:
            text = text[:197].rstrip() + "..."
        symbols_part = f"; {', '.join(lesson.sample_symbols)}" if lesson.sample_symbols else ""
        lines.append(f'  - "{text}" ({lesson.n_occurrences} veces{symbols_part})')
    lines.append("</recurring_lessons>")
    return "\n".join(lines)
