"""Dispatcher que invoca al `post_mortem_agent` cuando un setup cierra.

Diferencias vs review_dispatcher:
- **Idempotencia**: UNIQUE(trade_id) en `setup_post_mortems` + ON CONFLICT
  DO NOTHING. Sin cooldown porque es un evento terminal (uno por trade).
- **Pre-cómputo determinístico**: ANTES de invocar al agente computa MFE/MAE
  y `entry_vs_exit_delta` (re-corre el scorer en la vela de cierre). El
  agente recibe esos datos en el user prompt — su trabajo es interpretarlos,
  no derivarlos.
- **Persistencia**: `journal_trades.mfe_mae` (computado para todos los trades
  cerrados, incluso si el agente falla) + `setup_post_mortems` (output del
  agente).
- **Reusa** el canal Valkey `reviews:user:{user_id}` con `type='post_mortem'`
  para evitar un canal nuevo (frontend ya escucha ese canal).
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import text

from app.agent.deps import AgentDeps
from app.agent.models import PostMortem
from app.agent.post_mortem_agent import POST_MORTEM_MODEL_ID, get_post_mortem_agent
from app.agent.post_mortem_system_prompt import POST_MORTEM_SYSTEM_PROMPT_VERSION
from app.agent.tools.confluence import (
    compute_score_components,
    confluence_map_to_factor_snapshot_deterministic,
)
from app.core.broadcasting.pubsub import publish_json, reviews_channel
from app.core.config import get_settings
from app.core.db import session_scope
from app.market.ohlcv.repo import fetch_range
from app.storage.post_mortem_repo import insert_post_mortem

log = structlog.get_logger(__name__)

# Lazy semáforo global para acotar coste concurrente. Bursts ocurren cuando
# el régimen flipea y N setups cierran en la misma vela.
_concurrency_sem: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _concurrency_sem
    if _concurrency_sem is None:
        _concurrency_sem = asyncio.Semaphore(get_settings().post_mortem_concurrency)
    return _concurrency_sem


async def maybe_run_post_mortem(
    *,
    trade_id: str,
    user_id: str,
    trigger_kind: str,  # 'setup_closed_sl' | 'setup_closed_tp'
    candle_ts: datetime,
) -> str | None:
    """Fire-and-forget entry point. Devuelve el post_mortem_id si se insertó,
    None si otro worker llegó primero (idempotencia) o si el feature está
    deshabilitado.
    """
    settings = get_settings()
    if not getattr(settings, "post_mortem_enabled", False):
        log.debug(
            "post_mortem.disabled",
            trade_id=trade_id,
            trigger_kind=trigger_kind,
        )
        return None
    try:
        return await _maybe_run_post_mortem_inner(
            trade_id=trade_id,
            user_id=user_id,
            trigger_kind=trigger_kind,
            candle_ts=candle_ts,
            settings=settings,
        )
    except Exception as exc:
        log.warning(
            "post_mortem.failed",
            trade_id=trade_id,
            trigger_kind=trigger_kind,
            error=type(exc).__name__,
            message=str(exc)[:200],
        )
        return None


async def _maybe_run_post_mortem_inner(
    *,
    trade_id: str,
    user_id: str,
    trigger_kind: str,
    candle_ts: datetime,
    settings: Any,
) -> str | None:
    # 1) Fetch full closed trade row.
    async with session_scope() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT id::text, user_id, symbol, timeframe, side,
                           entry_px, stop_loss_px, exit_px, r_multiple,
                           targets, regime, confidence, summary_es_full,
                           confluences, scenarios, factor_snapshot,
                           entry_hit_at, closed_at
                    FROM journal_trades
                    WHERE id = CAST(:tid AS uuid)
                    """
                ),
                {"tid": trade_id},
            )
        ).mappings().one_or_none()
    if row is None:
        log.warning("post_mortem.trade_not_found", trade_id=trade_id)
        return None

    setup_data = _row_to_setup_data(dict(row))

    # 2) Compute MFE/MAE from OHLCV in window entry_hit_at → closed_at.
    mfe_mae = await _compute_mfe_mae(
        exchange=settings.exchange if hasattr(settings, "exchange") else "binance_usdm",
        symbol=setup_data["symbol"],
        timeframe=setup_data["timeframe"],
        side=setup_data["side"],
        entry_px=setup_data["entry_px"],
        stop_loss_px=setup_data["stop_loss_px"],
        entry_hit_at=setup_data["entry_hit_at"],
        closed_at=setup_data["closed_at"] or candle_ts,
        r_multiple=setup_data["r_multiple"],
    )
    # Persist mfe_mae on journal_trades regardless of agent outcome.
    if mfe_mae:
        async with session_scope() as session:
            await session.execute(
                text(
                    """
                    UPDATE journal_trades
                    SET mfe_mae = CAST(:mm AS jsonb)
                    WHERE id = CAST(:tid AS uuid)
                    """
                ),
                {"tid": trade_id, "mm": json.dumps(mfe_mae)},
            )

    # 3) Compute entry_vs_exit_delta (re-run confluence scorer at close time).
    entry_vs_exit_delta = await _compute_entry_vs_exit_delta(
        symbol=setup_data["symbol"],
        factor_snapshot=setup_data["factor_snapshot"],
        closed_at=setup_data["closed_at"] or candle_ts,
        exchange=getattr(settings, "exchange", "binance_usdm"),
    )

    # 4) Throttle global con semáforo.
    sem = _get_semaphore()
    async with sem:
        # 5) Build user prompt + deps + invoke agent.
        prompt = _build_post_mortem_prompt(
            setup_data=setup_data,
            trigger_kind=trigger_kind,
            mfe_mae=mfe_mae,
            entry_vs_exit_delta=entry_vs_exit_delta,
        )
        deps = AgentDeps(
            session_factory=session_scope,
            log=log,
            user_id=user_id,
        )

        log.info(
            "post_mortem.dispatched",
            trade_id=trade_id,
            user_id=user_id,
            symbol=setup_data["symbol"],
            trigger_kind=trigger_kind,
            r_multiple=setup_data["r_multiple"],
            mfe_r=(mfe_mae or {}).get("mfe_r"),
        )
        started = time.perf_counter()
        result = await get_post_mortem_agent().run(prompt, deps=deps)
        duration_ms = int((time.perf_counter() - started) * 1000)
        pm: PostMortem = result.output

    # 6) Extract usage + cost.
    usage_tokens, cost_usd = _extract_usage_and_cost(result, settings)

    # 7) Map verdict/outcome → DB fields and persist (idempotent ON CONFLICT).
    outcome = _outcome_from_r(setup_data["r_multiple"], trigger_kind)
    exit_reason = _exit_reason_from_trigger(trigger_kind)

    factor_verdicts = _build_factor_verdicts(
        snapshot=setup_data["factor_snapshot"],
        delta=entry_vs_exit_delta,
        success_factors=pm.success_factors,
        failure_factors=pm.failure_factors,
    )

    async with session_scope() as session:
        # Migración 015: what_worked/what_failed eliminados — los datos siguen
        # disponibles vía pm.success_factors / pm.failure_factors (output del
        # agente) y vía factor_verdicts (estructurado por factor).
        pm_id = await insert_post_mortem(
            session,
            trade_id=trade_id,
            user_id=user_id,
            outcome=outcome,
            r_multiple=float(setup_data["r_multiple"] or 0.0),
            exit_reason=exit_reason,
            verdict=pm.verdict,
            confidence_calibration=pm.confidence_calibration,
            factor_verdicts=factor_verdicts,
            lesson_es=pm.lesson_es,
            summary_es=pm.lesson_es,  # summary == lesson para post-mortem v1
            counterfactual_es=pm.counterfactual_es,
            entry_vs_exit_delta=entry_vs_exit_delta,
            citations=[c.model_dump(mode="json") for c in pm.citations],
            model_id=POST_MORTEM_MODEL_ID,
            usage_tokens=usage_tokens,
            cost_usd=cost_usd,
            prompt_version=POST_MORTEM_SYSTEM_PROMPT_VERSION,
        )

    if pm_id is None:
        # Otro worker ganó la carrera. Audit event ya escrito (o no se duplicó
        # por la guard del repo). Silencio aquí.
        log.debug("post_mortem.idempotency_collision", trade_id=trade_id)
        return None

    # 8) Publish al frontend (best-effort).
    try:
        await publish_json(
            reviews_channel(user_id),
            {
                "type": "post_mortem",
                "post_mortem_id": pm_id,
                "trade_id": trade_id,
                "symbol": setup_data["symbol"],
                "timeframe": setup_data["timeframe"],
                "side": setup_data["side"],
                "trigger_kind": trigger_kind,
                "outcome": outcome,
                "r_multiple": float(setup_data["r_multiple"] or 0.0),
                "verdict": pm.verdict,
                "confidence_calibration": pm.confidence_calibration,
                "success_factors": pm.success_factors,
                "failure_factors": pm.failure_factors,
                "lesson_es": pm.lesson_es,
                "counterfactual_es": pm.counterfactual_es,
                "mfe_mae": mfe_mae,
                "citations": [c.model_dump(mode="json") for c in pm.citations],
                "created_at": datetime.now(tz=UTC).isoformat(),
            },
        )
    except Exception as exc:
        log.warning(
            "post_mortem.publish_failed",
            post_mortem_id=pm_id,
            error=type(exc).__name__,
        )

    log.info(
        "post_mortem.completed",
        post_mortem_id=pm_id,
        trade_id=trade_id,
        verdict=pm.verdict,
        outcome=outcome,
        duration_ms=duration_ms,
        cost_usd=cost_usd,
    )
    return pm_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_setup_data(row: dict[str, Any]) -> dict[str, Any]:
    """Normaliza tipos (Decimal → float, JSON string → dict/list)."""
    import contextlib

    out: dict[str, Any] = dict(row)
    for k in ("entry_px", "stop_loss_px", "exit_px", "r_multiple"):
        if out.get(k) is not None:
            with contextlib.suppress(TypeError, ValueError):
                out[k] = float(out[k])
    for k in ("targets", "confluences", "scenarios", "factor_snapshot"):
        v = out.get(k)
        if isinstance(v, str):
            try:
                out[k] = json.loads(v)
            except Exception:
                out[k] = None
    return out


async def _compute_mfe_mae(
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
    side: str,
    entry_px: float | None,
    stop_loss_px: float | None,
    entry_hit_at: datetime | None,
    closed_at: datetime,
    r_multiple: float | None,
) -> dict[str, Any] | None:
    """Maximum Favorable / Adverse Excursion en R-units desde entry hasta close.

    R = |entry - stop_loss|. Para long: MFE = (max_high - entry) / R,
    MAE = (min_low - entry) / R. Inverso para short.

    Devuelve None si faltan datos críticos.
    """
    if entry_px is None or stop_loss_px is None or entry_hit_at is None:
        return None
    r_unit = abs(entry_px - stop_loss_px)
    if r_unit <= 0:
        return None

    async with session_scope() as session:
        candles = await fetch_range(
            session,
            exchange=exchange,
            symbol=symbol.upper(),
            timeframe=timeframe,
            since=entry_hit_at,
            until=closed_at,
            limit=5000,
        )
    if not candles:
        return None

    max_high = max(float(c.h) for c in candles)
    min_low = min(float(c.l) for c in candles)
    max_high_ts = next(c.ts for c in candles if float(c.h) == max_high)
    min_low_ts = next(c.ts for c in candles if float(c.l) == min_low)

    if side == "long":
        mfe_r = (max_high - entry_px) / r_unit
        mae_r = (min_low - entry_px) / r_unit
        mfe_at = max_high_ts
        mae_at = min_low_ts
    else:  # short
        mfe_r = (entry_px - min_low) / r_unit
        mae_r = (entry_px - max_high) / r_unit
        mfe_at = min_low_ts
        mae_at = max_high_ts

    def _hours_between(a: datetime, b: datetime) -> float:
        return round((a - b).total_seconds() / 3600.0, 2)

    exit_efficiency = None
    if mfe_r > 0 and r_multiple is not None:
        exit_efficiency = round(max(0.0, float(r_multiple) / mfe_r * 100.0), 1)

    return {
        "mfe_r": round(mfe_r, 3),
        "mae_r": round(mae_r, 3),
        "mfe_at": mfe_at.isoformat() if hasattr(mfe_at, "isoformat") else str(mfe_at),
        "mae_at": mae_at.isoformat() if hasattr(mae_at, "isoformat") else str(mae_at),
        "time_to_mfe_h": _hours_between(mfe_at, entry_hit_at),
        "time_to_mae_h": _hours_between(mae_at, entry_hit_at),
        "exit_efficiency_pct": exit_efficiency,
    }


async def _compute_entry_vs_exit_delta(
    *,
    symbol: str,
    factor_snapshot: dict[str, Any] | None,
    closed_at: datetime,
    exchange: str,
) -> dict[str, Any] | None:
    """Re-corre el scorer al momento del cierre y construye delta vs entry.

    Devuelve None si no había snapshot original (no podemos comparar).
    """
    if not factor_snapshot:
        return None
    entry_det = factor_snapshot.get("deterministic")
    if not isinstance(entry_det, dict):
        return None
    entry_by_tf = entry_det.get("by_tf") or {}
    if not isinstance(entry_by_tf, dict):
        return None

    try:
        cmap = await compute_score_components(
            session_factory=session_scope,
            exchange=exchange,
            symbol=symbol,
            until=closed_at,
        )
    except Exception:
        return None
    exit_det = confluence_map_to_factor_snapshot_deterministic(cmap)
    exit_by_tf = exit_det.get("by_tf") or {}

    delta_by_tf: dict[str, dict[str, float]] = {}
    for tf, entry_row in entry_by_tf.items():
        if not isinstance(entry_row, dict):
            continue
        exit_row = exit_by_tf.get(tf) or {}
        delta_row: dict[str, float] = {}
        for fname, ev in entry_row.items():
            if fname == "score_total":
                continue
            try:
                ev_f = float(ev)
                xv_f = float(exit_row.get(fname, 0.0))
            except (TypeError, ValueError):
                continue
            delta_row[fname] = round(xv_f - ev_f, 3)
        if delta_row:
            delta_by_tf[tf] = delta_row

    return {
        "entry": entry_det,
        "exit": exit_det,
        "delta_by_tf": delta_by_tf,
        "regime_changed": (
            entry_det.get("aggregate_bias") != exit_det.get("aggregate_bias")
        ),
    }


def _build_factor_verdicts(
    *,
    snapshot: dict[str, Any] | None,
    delta: dict[str, Any] | None,
    success_factors: list[str],
    failure_factors: list[str],
) -> dict[str, Any]:
    """Construye `factor_verdicts` para persistir: {"ema_stack@1h": {value,
    verdict: 'worked'|'failed'|'neutral', delta?}, ...}.

    El agente clasifica vía success/failure_factors lists; aquí enriquecemos
    con el valor real y delta para que el frontend pueda renderizar chips.
    """
    out: dict[str, Any] = {}
    if not snapshot:
        return out
    det = snapshot.get("deterministic") or {}
    by_tf = det.get("by_tf") or {}

    success_set = set(success_factors)
    failure_set = set(failure_factors)
    delta_by_tf = (delta or {}).get("delta_by_tf") or {}

    if isinstance(by_tf, dict):
        for tf, components in by_tf.items():
            if not isinstance(components, dict):
                continue
            for fname, value in components.items():
                if fname == "score_total":
                    continue
                key = f"{fname}@{tf}"
                verdict = (
                    "worked" if key in success_set
                    else "failed" if key in failure_set
                    else "neutral"
                )
                entry = {"value": float(value), "verdict": verdict}
                if isinstance(delta_by_tf.get(tf), dict):
                    d = delta_by_tf[tf].get(fname)
                    if d is not None:
                        entry["delta"] = float(d)
                out[key] = entry

    # Semantic tags
    for tag in snapshot.get("semantic_tags") or []:
        if not isinstance(tag, str):
            continue
        verdict = (
            "worked" if tag in success_set
            else "failed" if tag in failure_set
            else "neutral"
        )
        out[tag] = {"verdict": verdict}

    return out


def _outcome_from_r(r_multiple: float | None, trigger_kind: str) -> str:
    if r_multiple is None:
        return "loss"
    if trigger_kind == "setup_closed_tp":
        if r_multiple > 0.2:
            return "win"
        return "breakeven"
    # setup_closed_sl
    return "loss" if r_multiple <= 0 else "breakeven"


def _exit_reason_from_trigger(trigger_kind: str) -> str:
    if trigger_kind == "setup_closed_sl":
        return "sl_hit"
    if trigger_kind == "setup_closed_tp":
        return "tp_hit"
    return "manual_close"


def _build_post_mortem_prompt(
    *,
    setup_data: dict[str, Any],
    trigger_kind: str,
    mfe_mae: dict[str, Any] | None,
    entry_vs_exit_delta: dict[str, Any] | None,
) -> str:
    """User message con TODO el contexto del post-mortem. NUNCA en el system
    prompt (rompería el cache)."""
    targets_block = _format_targets(setup_data.get("targets") or [])
    factor_snapshot = setup_data.get("factor_snapshot") or {}
    snapshot_keys_block = _format_snapshot_keys(factor_snapshot)
    mfe_block = (
        f"  - MFE: {mfe_mae['mfe_r']:+.2f}R en {mfe_mae['time_to_mfe_h']}h "
        f"(ts {mfe_mae['mfe_at']})\n"
        f"  - MAE: {mfe_mae['mae_r']:+.2f}R en {mfe_mae['time_to_mae_h']}h "
        f"(ts {mfe_mae['mae_at']})\n"
        f"  - Exit efficiency: {mfe_mae.get('exit_efficiency_pct')}%"
        if mfe_mae
        else "  (sin OHLCV disponible)"
    )
    delta_block = _format_delta_block(entry_vs_exit_delta)
    thesis_block = _format_thesis_block(setup_data)

    return (
        f"Trade cerrado para análisis post-mortem:\n"
        f"- Setup id: {setup_data['id']}\n"
        f"- Symbol: {setup_data['symbol']} ({setup_data['timeframe']})\n"
        f"- Side: {setup_data['side']}\n"
        f"- Entry: {setup_data['entry_px']} (entry_hit_at {setup_data.get('entry_hit_at')})\n"
        f"- SL: {setup_data['stop_loss_px']}\n"
        f"- Exit: {setup_data.get('exit_px')} (closed_at {setup_data.get('closed_at')})\n"
        f"- R-multiple final: {setup_data.get('r_multiple')}\n"
        f"- Targets:\n{targets_block}\n"
        f"\n"
        f"Trigger: {trigger_kind}\n"
        f"\n"
        f"MFE/MAE:\n{mfe_block}\n"
        f"\n"
        f"{thesis_block}\n"
        f"\n"
        f"Factor snapshot al ENTRY (claves VÁLIDAS para success/failure):\n"
        f"{snapshot_keys_block}\n"
        f"\n"
        f"Entry vs exit delta (ScoreComponents al cerrar):\n{delta_block}\n"
        f"\n"
        f"Tu trabajo: emite un PostMortem siguiendo tu system prompt. Las "
        f"claves de success_factors/failure_factors DEBEN venir de la lista "
        f"anterior. Usa MFE/MAE para distinguir thesis_held / thesis_broken / "
        f"execution_error / noise. setup_id = {setup_data['id']}."
    )


def _format_targets(targets: list[dict[str, Any]]) -> str:
    if not targets:
        return "  (sin targets)"
    return "\n".join(
        f"  - {t.get('label', '?')}: {t.get('price', '?')}"
        f" {'(hit ' + str(t.get('hit_at')) + ')' if t.get('hit_at') else '(no hit)'}"
        for t in targets
    )


def _format_snapshot_keys(snapshot: dict[str, Any]) -> str:
    keys: list[str] = []
    det = snapshot.get("deterministic") or {}
    by_tf = det.get("by_tf") or {}
    if isinstance(by_tf, dict):
        for tf, comp in by_tf.items():
            if isinstance(comp, dict):
                for fname in comp:
                    if fname == "score_total":
                        continue
                    keys.append(f"{fname}@{tf}")
    for tag in snapshot.get("semantic_tags") or []:
        if isinstance(tag, str):
            keys.append(tag)
    if not keys:
        return "  (sin snapshot — usa 'noise' o atribuye al contexto general)"
    return "  - " + "\n  - ".join(sorted(keys))


def _format_delta_block(delta: dict[str, Any] | None) -> str:
    if not delta:
        return "  (sin delta — no había factor_snapshot original)"
    lines: list[str] = []
    delta_by_tf = delta.get("delta_by_tf") or {}
    if isinstance(delta_by_tf, dict):
        for tf, row in sorted(delta_by_tf.items()):
            if not isinstance(row, dict):
                continue
            parts = [
                f"{fname}={dv:+.2f}"
                for fname, dv in row.items()
                if abs(dv) >= 0.1  # ruido por debajo de 0.1 lo omitimos
            ]
            if parts:
                lines.append(f"  [{tf}] " + ", ".join(parts))
    if delta.get("regime_changed"):
        lines.append("  ⚠ aggregate_bias FLIPEÓ entre entry y exit")
    return "\n".join(lines) if lines else "  (sin cambios significativos en factores)"


def _format_thesis_block(setup_data: dict[str, Any]) -> str:
    parts: list[str] = ["Tesis original del setup:"]
    if setup_data.get("regime"):
        parts.append(f"- régimen: {setup_data['regime']}")
    if setup_data.get("confidence"):
        parts.append(f"- confidence: {setup_data['confidence']}")
    if setup_data.get("summary_es_full"):
        parts.append(f"- Resumen:\n  {setup_data['summary_es_full']}")
    confluences = setup_data.get("confluences") or []
    if confluences:
        parts.append("- Confluencias originales:")
        for c in confluences:
            parts.append(
                f"  - [{c.get('timeframe', '?')}] {c.get('bias', '?')}: "
                f"{c.get('narrative', '')[:160]}"
            )
    return "\n".join(parts)


def _extract_usage_and_cost(
    result: Any, settings: Any
) -> tuple[dict[str, Any] | None, float | None]:
    """Best-effort. Reutiliza pricing flags configurados para review (post-
    mortem corre el mismo modelo)."""
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

    usage_tokens = {
        "input": input_t,
        "output": output_t,
        "cache_read": cache_read,
        "cache_create": cache_write,
        "total": input_t + output_t,
    }

    in_per_m = getattr(settings, "review_price_input_per_m_usd", 3.0)
    out_per_m = getattr(settings, "review_price_output_per_m_usd", 15.0)
    cache_per_m = getattr(settings, "review_price_cache_read_per_m_usd", 0.3)
    chargeable_input = max(input_t - cache_read, 0)
    cost = (
        chargeable_input * in_per_m / 1_000_000
        + cache_read * cache_per_m / 1_000_000
        + output_t * out_per_m / 1_000_000
    )
    return usage_tokens, round(cost, 6)
