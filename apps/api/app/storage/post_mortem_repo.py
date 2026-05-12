"""Repository para `setup_post_mortems` — análisis terminal al cierre de un setup.

Operación principal: `insert_post_mortem` con `ON CONFLICT DO NOTHING` sobre
la UNIQUE(trade_id) — garantiza exactamente un post-mortem por trade aunque
el dispatcher se ejecute dos veces (race entre dos workers o reentry tras
restart). No usa cooldown (es un evento terminal, no recurrente).

A.3 (plan integral 2026-05-11): `get_recurring_lessons_for_preamble`
identifica patrones de error que se repiten en el historial reciente del
usuario bajo el régimen actual, para inyectarse al preamble del chat. Las
lecciones se agrupan por *fingerprint* (bag-of-significant-keywords) para
contar variantes del mismo patrón como UN solo cluster.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def insert_post_mortem(
    session: AsyncSession,
    *,
    trade_id: str,
    user_id: str,
    outcome: str,
    r_multiple: float,
    exit_reason: str,
    verdict: str,
    confidence_calibration: str,
    factor_verdicts: dict[str, Any],
    lesson_es: str,
    summary_es: str,
    counterfactual_es: str | None,
    entry_vs_exit_delta: dict[str, Any] | None,
    citations: list[dict[str, Any]],
    model_id: str,
    usage_tokens: dict[str, Any] | None,
    cost_usd: float | None,
    prompt_version: str | None,
) -> str | None:
    """Inserta el post-mortem y graba `setup_events.event='review_generated'`
    como audit (mismo evento que reviews para que el timeline UI lo renderice).

    Idempotente vía UNIQUE(trade_id). Si otra ejecución llegó primero, devuelve
    None — el caller debe abortar silencioso sin escribir audit event duplicado.

    Nota (migración 015): los lists `what_worked`/`what_failed` fueron eliminados
    del schema — eran copia literal de `success_factors`/`failure_factors` que
    ya viven en `factor_verdicts` JSONB. Reconstruibles desde ahí si se
    necesitan.
    """
    inserted = (
        await session.execute(
            text(
                """
                INSERT INTO setup_post_mortems (
                    trade_id, user_id, outcome, r_multiple, exit_reason,
                    verdict, confidence_calibration, factor_verdicts,
                    lesson_es, summary_es, counterfactual_es,
                    entry_vs_exit_delta, citations,
                    model_id, usage_tokens, cost_usd, prompt_version
                ) VALUES (
                    CAST(:tid AS uuid), :uid, :outcome, :r, :exit_reason,
                    :verdict, :cal, CAST(:fv AS jsonb),
                    :lesson, :summary, :counterfactual,
                    CAST(:delta AS jsonb), CAST(:citations AS jsonb),
                    :model, CAST(:usage AS jsonb), :cost, :pv
                )
                ON CONFLICT (trade_id) DO NOTHING
                RETURNING id::text
                """
            ),
            {
                "tid": trade_id,
                "uid": user_id,
                "outcome": outcome,
                "r": r_multiple,
                "exit_reason": exit_reason,
                "verdict": verdict,
                "cal": confidence_calibration,
                "fv": json.dumps(factor_verdicts),
                "lesson": lesson_es,
                "summary": summary_es,
                "counterfactual": counterfactual_es,
                "delta": (
                    json.dumps(entry_vs_exit_delta) if entry_vs_exit_delta is not None else None
                ),
                "citations": json.dumps(citations),
                "model": model_id,
                "usage": json.dumps(usage_tokens) if usage_tokens else None,
                "cost": cost_usd,
                "pv": prompt_version,
            },
        )
    ).scalar_one_or_none()

    if inserted is None:
        # Otro worker ganó la carrera. Audit event no se duplica.
        return None

    await session.execute(
        text(
            """
            INSERT INTO setup_events (trade_id, event, candle_ts, payload)
            VALUES (
                CAST(:tid AS uuid), 'review_generated', now(),
                CAST(:payload AS jsonb)
            )
            """
        ),
        {
            "tid": trade_id,
            "payload": json.dumps(
                {
                    "post_mortem_id": inserted,
                    "kind": "post_mortem",
                    "outcome": outcome,
                    "verdict": verdict,
                    "lesson_es": lesson_es,
                    "r_multiple": r_multiple,
                }
            ),
        },
    )

    return str(inserted)


async def get_post_mortem_by_trade_id(
    session: AsyncSession,
    *,
    trade_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Single post-mortem por trade. Scope por user_id (defensa en profundidad)."""
    row = (
        (
            await session.execute(
                text(
                    """
                SELECT id::text, trade_id::text, user_id, outcome, r_multiple,
                       exit_reason, verdict, confidence_calibration,
                       factor_verdicts,
                       lesson_es, summary_es, counterfactual_es,
                       entry_vs_exit_delta, citations,
                       model_id, usage_tokens, cost_usd, prompt_version,
                       created_at
                FROM setup_post_mortems
                WHERE trade_id = CAST(:tid AS uuid) AND user_id = :uid
                """
                ),
                {"tid": trade_id, "uid": user_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if not row:
        return None
    return _normalize_row(dict(row))


async def list_post_mortems(
    session: AsyncSession,
    *,
    user_id: str,
    outcome: str | None = None,
    verdict: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Lista paginada ordenada por created_at DESC. Filtros opcionales por
    outcome (win/loss/breakeven/partial_win) y verdict (thesis_held/...)."""
    sql = """
        SELECT id::text, trade_id::text, user_id, outcome, r_multiple,
               exit_reason, verdict, confidence_calibration,
               factor_verdicts,
               lesson_es, summary_es, counterfactual_es,
               entry_vs_exit_delta, citations,
               model_id, usage_tokens, cost_usd, prompt_version, created_at
        FROM setup_post_mortems
        WHERE user_id = :uid
    """
    params: dict[str, Any] = {"uid": user_id, "lim": limit}
    if outcome:
        sql += " AND outcome = :outcome"
        params["outcome"] = outcome
    if verdict:
        sql += " AND verdict = :verdict"
        params["verdict"] = verdict
    sql += " ORDER BY created_at DESC LIMIT :lim"
    rows = (await session.execute(text(sql), params)).mappings().all()
    return [_normalize_row(dict(r)) for r in rows]


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    if out.get("r_multiple") is not None:
        out["r_multiple"] = float(out["r_multiple"])
    if out.get("cost_usd") is not None:
        out["cost_usd"] = float(out["cost_usd"])
    # JSONB columnas: en algunos drivers vienen como dict/list ya parseados,
    # en otros como string. Normalizar.
    for jsonb_key in (
        "factor_verdicts",
        "entry_vs_exit_delta",
        "citations",
        "usage_tokens",
    ):
        v = out.get(jsonb_key)
        if isinstance(v, str):
            try:
                out[jsonb_key] = json.loads(v)
            except Exception:
                out[jsonb_key] = None
    return out


# -----------------------------------------------------------------------------
# A.3 — Recurring lessons preamble (plan integral 2026-05-11)
# -----------------------------------------------------------------------------
#
# Post-mortems emit a `lesson_es` per closed trade — actionable one-liner the
# agent would benefit from seeing on its next turn so the same mistake doesn't
# repeat. Without this, those lessons sit in the table and never reach the
# decision loop.
#
# Implementation:
# - Fetch the last N closed trades' post-mortems with verdict='thesis_broken'
#   (those carry the strongest signal — "tu tesis se rompió aquí").
# - Cluster near-duplicate lessons by *fingerprint* (sorted bag of significant
#   keywords). Wording variations of the same pattern collapse into one cluster.
# - Return the top_k clusters by (occurrences DESC, recency DESC) with the
#   most-recent exemplar as the displayed text.
#
# Clustering is intentionally heuristic — character-level shingles or
# embeddings would be more accurate but require dependencies/infra we'd
# rather not pay for at preamble-build time (called every chat turn).

# Stopwords ES más comunes en lessons de trading. Lista mínima — la
# arquitectura es robusta: lessons sin keywords significativas igual
# producen fingerprint (vacío o muy corto, que las agrupa también).
_LESSON_STOPWORDS_ES: frozenset[str] = frozenset(
    {
        "para",
        "como",
        "porque",
        "cuando",
        "donde",
        "esta",
        "este",
        "esto",
        "estos",
        "estas",
        "haber",
        "tener",
        "siempre",
        "nunca",
        "tambien",
        "ademas",
        "pero",
        "aunque",
        "mientras",
        "luego",
        "antes",
        "despues",
        "sobre",
        "entre",
        "hasta",
        "desde",
        "hacia",
        "contra",
        "segun",
        "todo",
        "todos",
        "toda",
        "todas",
        "solo",
        "tanto",
        "tanta",
        "poco",
        "poca",
        "algun",
        "alguna",
        "algunos",
        "ninguno",
        "ninguna",
        "cualquier",
        "cualquiera",
        # Light verb noise — they don't carry semantic load in trading lessons.
        "puede",
        "debe",
        "deben",
        "hace",
        "hacer",
        "hizo",
        "fue",
        "siendo",
    }
)


class RecurringLesson(BaseModel):
    """A cluster of similar `lesson_es` strings observed in recent post-mortems
    of the user. The exemplar shown is the most recent text in the cluster."""

    lesson_es: str
    n_occurrences: int
    last_seen_at: datetime
    sample_symbols: list[str]
    fingerprint: str


def _lesson_fingerprint(lesson_es: str) -> str:
    """Canonical bag-of-keywords used to bucket similar lessons.

    Steps:
    1. Strip diacritics (NFKD), lowercase.
    2. Tokenize into alphanumeric word runs.
    3. Keep tokens of length ≥ 4 that aren't in `_LESSON_STOPWORDS_ES`.
    4. Take the first 8 significant tokens (their order in the lesson is
       a weak proxy for what the agent leads with), then keep the top 6 in
       alphabetical order so wording reordering doesn't break the cluster.

    Returns `"|".join(...)` — a deterministic string usable as a dict key.
    Empty string is a valid fingerprint (every lesson with no significant
    keywords collapses into one group).
    """
    if not lesson_es:
        return ""
    norm = unicodedata.normalize("NFKD", lesson_es).lower()
    norm = "".join(c for c in norm if not unicodedata.combining(c))
    tokens = re.findall(r"[a-z0-9_]+", norm)
    significant = [t for t in tokens if len(t) >= 4 and t not in _LESSON_STOPWORDS_ES]
    head = significant[:8]
    top = sorted(head)[:6]
    return "|".join(top)


def _cluster_lessons(
    rows: list[dict[str, Any]],
    *,
    top_k: int,
    min_occurrences: int,
) -> list[RecurringLesson]:
    """Group rows by fingerprint, return the top_k clusters by
    (n_occurrences DESC, last_seen_at DESC).

    Each row is expected to expose `lesson_es: str`, `symbol: str`,
    `created_at: datetime`. Rows lacking those keys are skipped.

    `min_occurrences` is the floor for inclusion — when the user's history is
    sparse and no cluster repeats, returning N singletons would just feel
    like spam in the preamble. Default 2 forces at least one repetition;
    raise the bar by increasing it.
    """
    buckets: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        lesson = r.get("lesson_es")
        if not isinstance(lesson, str) or not lesson.strip():
            continue
        fp = _lesson_fingerprint(lesson)
        buckets.setdefault(fp, []).append(r)

    clusters: list[RecurringLesson] = []
    for fp, group in buckets.items():
        if len(group) < min_occurrences:
            continue
        # Sort group by created_at DESC so the exemplar shown is the most recent.
        group_sorted = sorted(
            group,
            key=lambda r: r.get("created_at") or datetime.min,
            reverse=True,
        )
        latest = group_sorted[0]
        symbols: list[str] = []
        seen_syms: set[str] = set()
        for r in group_sorted:
            sym = r.get("symbol")
            if isinstance(sym, str) and sym and sym not in seen_syms:
                seen_syms.add(sym)
                symbols.append(sym)
            if len(symbols) >= 3:
                break
        clusters.append(
            RecurringLesson(
                lesson_es=str(latest["lesson_es"]),
                n_occurrences=len(group),
                last_seen_at=latest["created_at"],
                sample_symbols=symbols,
                fingerprint=fp,
            )
        )

    clusters.sort(
        key=lambda c: (c.n_occurrences, c.last_seen_at),
        reverse=True,
    )
    return clusters[:top_k]


async def get_recurring_lessons_for_preamble(
    session: AsyncSession,
    *,
    user_id: str,
    regime_label: str | None = None,
    n_lookback: int = 50,
    top_k: int = 3,
    min_occurrences: int = 2,
) -> list[RecurringLesson]:
    """Top-K recurring `thesis_broken` lessons from the user's last N
    post-mortems, optionally scoped to the current regime.

    Empty list when no cluster meets `min_occurrences` — the caller should
    skip the preamble block in that case rather than emit a noisy
    "no recurring lessons" line.

    The query joins `setup_post_mortems` to `journal_trades` for symbol +
    regime context. Regime filtering is loose: matches either the trade's
    regime label OR (if the post-mortem stored its own regime context in
    `factor_verdicts.context.regime_label`) that one.
    """
    where_clauses = [
        "pm.user_id = :uid",
        "pm.verdict = 'thesis_broken'",
    ]
    params: dict[str, Any] = {"uid": user_id, "lim": n_lookback}
    if regime_label:
        where_clauses.append(
            "(jt.regime = :rl OR pm.factor_verdicts->'context'->>'regime_label' = :rl)"
        )
        params["rl"] = regime_label

    where_sql = " AND ".join(where_clauses)
    sql = f"""
        SELECT pm.lesson_es, pm.created_at, jt.symbol
        FROM setup_post_mortems pm
        JOIN journal_trades jt ON jt.id = pm.trade_id
        WHERE {where_sql}
        ORDER BY pm.created_at DESC
        LIMIT :lim
    """
    rows = (await session.execute(text(sql), params)).mappings().all()
    return _cluster_lessons([dict(r) for r in rows], top_k=top_k, min_occurrences=min_occurrences)


__all__ = [
    "RecurringLesson",
    "_cluster_lessons",
    "_lesson_fingerprint",
    "get_post_mortem_by_trade_id",
    "get_recurring_lessons_for_preamble",
    "insert_post_mortem",
    "list_post_mortems",
]
