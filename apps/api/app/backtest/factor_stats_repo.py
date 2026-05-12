"""Repository de agregación sobre `factor_outcomes` con Bayesian win-rate.

Cada trade cerrado contribuye N filas a `factor_outcomes` (una por factor
deterministic activo + una por tag semántico). Este módulo agrega esas filas
para responder a la pregunta clave del feedback loop:

    "¿Cuál es el win-rate histórico del factor X bajo régimen Y para el
     usuario U en los últimos D días, excluyendo holdout?"

## Bayesian win-rate (EXT-1)

Reportar `wins/n` con `n=3` engaña: 2/3 = 67% es ruido puro.

Usamos Beta-Binomial conjugate prior `Beta(α₀=2, β₀=2)`:
- Posterior tras observar (wins, losses): `Beta(α₀+wins, β₀+losses)`.
- `win_rate_mean = (α₀+wins) / (α₀+β₀+n)` — posterior expected value.
- `win_rate_lcb` = 5th percentile del posterior — el "trust floor".
- `win_rate_ucb` = 95th percentile del posterior.

Política conservadora: el preamble usa `win_rate_lcb`, el validator soft
gate dispara cuando `win_rate_lcb < 25%`. Esto penaliza naturalmente la
incertidumbre — un factor con `n=3, wins=2` reporta `mean≈58%, lcb≈22%`
y no pasa el filtro.

`scipy.stats.beta.ppf` es la fuente de verdad para los percentiles.

## Holdout (EXT-4)

Default `include_holdout=False`: las queries de feedback loop NUNCA tocan
trades marcados `is_holdout=TRUE`. El endpoint `/holdout-performance` los
consulta explícitamente con `include_holdout=True, only_holdout=True` para
detectar drift in-sample vs holdout.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

import structlog
from pydantic import BaseModel
from scipy.stats import beta as beta_dist
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.models import FactorBlock, GateVerdict

logger = structlog.get_logger()

# Prior conservador. Beta(2,2) tiene media 0.5 y varianza modesta; con
# `n=0` retorna lcb≈9%, ucb≈91% — el agente verá "sin evidencia" claramente.
# Subirlo a Beta(5,5) sería demasiado fuerte (n=10 movería poco la posterior).
PRIOR_ALPHA = 2.0
PRIOR_BETA = 2.0

# Threshold de presencia para factores deterministic en factor_outcomes.
# |value| >= este umbral → factor_present=TRUE (cuenta para win-rate).
# Bajo este umbral el factor estaba "neutro" — no contribuye señal.
PRESENT_THRESHOLD = 0.4

# Factor gate progressive thresholds (A.2 — plan integral 2026-05-11).
# The gate inspects every factor a TradeIdea is RELYING on (deterministic
# factor with |value| >= PRESENT_THRESHOLD, or any semantic tag emitted)
# and consults their Bayesian win-rate LCB under the current regime.
#
# Rationale for keying on win_rate_lcb (not mean): the LCB penalizes
# uncertainty — a factor with `n=10, wins=4` has mean≈42% but lcb≈18%, so
# we won't act as if the factor "has 42% WR" until we have evidence.
FACTOR_GATE_SOFT_MIN_N = 30  # below this → advisory only
FACTOR_GATE_HARD_MIN_N = 100  # at or above → hard veto eligible
FACTOR_GATE_SOFT_LCB_THRESHOLD = 0.35  # in 30 ≤ n < 100 band
FACTOR_GATE_HARD_LCB_THRESHOLD = 0.30  # in n ≥ 100 band


# -----------------------------------------------------------------------------
# Public types
# -----------------------------------------------------------------------------


class FactorHitRate(BaseModel):
    """Stats agregadas para un (factor_name, factor_tf) bajo filtros dados."""

    factor_name: str
    factor_tf: str | None
    factor_kind: Literal["deterministic", "semantic"]
    n_trades: int
    n_wins: int
    win_rate_mean: float  # posterior E[θ] = (α₀+wins)/(α₀+β₀+n)
    win_rate_lcb: float  # 5th percentile (decision-driving)
    win_rate_ucb: float  # 95th percentile
    avg_r: float | None
    expectancy_r: float | None  # E[R] = sum(r_multiple) / n
    last_closed_at: datetime | None
    # Prior usado — explícito en la respuesta para que el agente entienda
    # que es Bayesian, no raw win-rate.
    prior_alpha: float = PRIOR_ALPHA
    prior_beta: float = PRIOR_BETA


class CombinedStat(BaseModel):
    """Stats para una conjunción AND de factores (ej: ema_stack + volume)."""

    factors: list[str]
    n_trades: int
    n_wins: int
    win_rate_mean: float
    win_rate_lcb: float
    win_rate_ucb: float
    expectancy_r: float | None


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _posterior_stats(
    *, wins: int, n: int, alpha: float = PRIOR_ALPHA, beta_: float = PRIOR_BETA
) -> tuple[float, float, float]:
    """Devuelve (mean, lcb, ucb) del Beta(α₀+wins, β₀+(n-wins)) posterior.

    `wins` y `n-wins` deben ser >=0. `n=0` retorna stats del prior puro:
    `mean=α₀/(α₀+β₀)=0.5`, lcb/ucb dependen del prior.
    """
    if wins < 0 or n < 0 or wins > n:
        raise ValueError(f"invalid wins={wins} n={n}")
    a = alpha + float(wins)
    b = beta_ + float(n - wins)
    mean = a / (a + b)
    # scipy.stats.beta.ppf devuelve numpy scalar; cast a float para JSON.
    lcb = float(beta_dist.ppf(0.05, a, b))
    ucb = float(beta_dist.ppf(0.95, a, b))
    return mean, lcb, ucb


# -----------------------------------------------------------------------------
# Main queries
# -----------------------------------------------------------------------------


async def get_factor_hit_rates(
    session: AsyncSession,
    *,
    user_id: str,
    factors: list[str] | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    regime_label: str | None = None,
    lookback_days: int | None = None,
    include_holdout: bool = False,
    only_holdout: bool = False,
    factor_kind: Literal["deterministic", "semantic"] | None = None,
) -> list[FactorHitRate]:
    """Win-rate Bayesian por (factor_name, factor_tf).

    Solo cuenta filas con `factor_present=TRUE` — un factor "ausente" del
    trade no contribuye a su win-rate. Esta es la semántica que el agente
    espera: "cuando ema_stack@1h estuvo presente, ganaste X% de las veces".

    Holdout: por defecto `include_holdout=False` excluye trades marcados
    como holdout. `only_holdout=True` los devuelve EXCLUSIVAMENTE (usado por
    el endpoint de monitoring).
    """
    where_clauses = ["user_id = :uid", "factor_present = TRUE"]
    params: dict[str, Any] = {"uid": user_id}

    if not include_holdout and not only_holdout:
        where_clauses.append("is_holdout = FALSE")
    elif only_holdout:
        where_clauses.append("is_holdout = TRUE")

    if factors:
        where_clauses.append("factor_name = ANY(:factors)")
        params["factors"] = list(factors)
    if symbol:
        where_clauses.append("symbol = :symbol")
        params["symbol"] = symbol.upper()
    if timeframe:
        where_clauses.append("timeframe = :tf")
        params["tf"] = timeframe
    if regime_label:
        where_clauses.append("regime_label = :rl")
        params["rl"] = regime_label
    if factor_kind:
        where_clauses.append("factor_kind = :fk")
        params["fk"] = factor_kind
    if lookback_days is not None:
        where_clauses.append("closed_at >= now() - make_interval(days => :lb)")
        params["lb"] = lookback_days

    where_sql = " AND ".join(where_clauses)
    sql = f"""
        SELECT
            factor_name,
            factor_tf,
            factor_kind,
            COUNT(*) AS n_trades,
            COUNT(*) FILTER (WHERE r_multiple > 0.2) AS n_wins,
            AVG(r_multiple) AS avg_r,
            SUM(r_multiple) / NULLIF(COUNT(*), 0) AS expectancy_r,
            MAX(closed_at) AS last_closed_at
        FROM factor_outcomes
        WHERE {where_sql}
        GROUP BY factor_name, factor_tf, factor_kind
        ORDER BY n_trades DESC
    """
    rows = (await session.execute(text(sql), params)).mappings().all()

    out: list[FactorHitRate] = []
    for r in rows:
        n = int(r["n_trades"])
        w = int(r["n_wins"])
        mean, lcb, ucb = _posterior_stats(wins=w, n=n)
        out.append(
            FactorHitRate(
                factor_name=r["factor_name"],
                factor_tf=r["factor_tf"],
                factor_kind=r["factor_kind"],
                n_trades=n,
                n_wins=w,
                win_rate_mean=mean,
                win_rate_lcb=lcb,
                win_rate_ucb=ucb,
                avg_r=float(r["avg_r"]) if r["avg_r"] is not None else None,
                expectancy_r=(float(r["expectancy_r"]) if r["expectancy_r"] is not None else None),
                last_closed_at=r["last_closed_at"],
            )
        )
    return out


async def get_combined_hit_rate(
    session: AsyncSession,
    *,
    user_id: str,
    factors: list[str],
    symbol: str | None = None,
    regime_label: str | None = None,
    lookback_days: int | None = None,
    include_holdout: bool = False,
) -> CombinedStat:
    """Win-rate Bayesian de la CONJUNCIÓN de varios factores (todos presentes
    en el mismo trade). Implementado vía GROUP BY trade_id HAVING COUNT(...)
    = n para encontrar trades donde TODOS los factores listados estuvieron
    presentes.
    """
    if not factors:
        raise ValueError("factors must be non-empty")

    where = ["user_id = :uid", "factor_present = TRUE", "factor_name = ANY(:factors)"]
    params: dict[str, Any] = {"uid": user_id, "factors": list(factors)}
    if not include_holdout:
        where.append("is_holdout = FALSE")
    if symbol:
        where.append("symbol = :symbol")
        params["symbol"] = symbol.upper()
    if regime_label:
        where.append("regime_label = :rl")
        params["rl"] = regime_label
    if lookback_days is not None:
        where.append("closed_at >= now() - make_interval(days => :lb)")
        params["lb"] = lookback_days
    where_sql = " AND ".join(where)
    params["n_factors"] = len(set(factors))

    # 1. Sub-query: trades donde TODOS los factores listados están presentes.
    # 2. JOIN con journal_trades para r_multiple a nivel trade (no por factor).
    sql = f"""
        WITH matching_trades AS (
            SELECT trade_id
            FROM factor_outcomes
            WHERE {where_sql}
            GROUP BY trade_id
            HAVING COUNT(DISTINCT factor_name) = :n_factors
        )
        SELECT
            COUNT(*) AS n_trades,
            COUNT(*) FILTER (WHERE jt.r_multiple > 0.2) AS n_wins,
            SUM(jt.r_multiple) / NULLIF(COUNT(*), 0) AS expectancy_r
        FROM matching_trades mt
        JOIN journal_trades jt ON jt.id = mt.trade_id
        WHERE jt.status = 'closed' AND jt.r_multiple IS NOT NULL
    """
    row = (await session.execute(text(sql), params)).mappings().one()
    n = int(row["n_trades"] or 0)
    w = int(row["n_wins"] or 0)
    mean, lcb, ucb = _posterior_stats(wins=w, n=n)
    return CombinedStat(
        factors=list(factors),
        n_trades=n,
        n_wins=w,
        win_rate_mean=mean,
        win_rate_lcb=lcb,
        win_rate_ucb=ucb,
        expectancy_r=(float(row["expectancy_r"]) if row["expectancy_r"] is not None else None),
    )


async def get_top_factors_for_preamble(
    session: AsyncSession,
    *,
    user_id: str,
    regime_label: str | None = None,
    lookback_days: int = 180,
    limit: int = 8,
    min_n: int = 3,
) -> list[FactorHitRate]:
    """Mix de factores top + bottom para el preamble de `chat.py`.

    Estrategia:
    - Trae todos los factores con `n_trades >= min_n` bajo el régimen dado
      (o global si `regime_label=None`).
    - Ordena por `win_rate_lcb DESC` y toma los top half y bottom half del
      límite. El agente ve tanto lo que funciona (sesgo a favor) como lo
      que falla (caveat explícito).
    """
    all_factors = await get_factor_hit_rates(
        session,
        user_id=user_id,
        regime_label=regime_label,
        lookback_days=lookback_days,
    )
    filtered = [f for f in all_factors if f.n_trades >= min_n]
    filtered.sort(key=lambda f: f.win_rate_lcb, reverse=True)

    if len(filtered) <= limit:
        return filtered

    half = max(1, limit // 2)
    top = filtered[:half]
    bottom = filtered[-(limit - half) :]
    return top + bottom


# -----------------------------------------------------------------------------
# Holdout performance (EXT-4 monitoring)
# -----------------------------------------------------------------------------


async def get_holdout_performance_summary(
    session: AsyncSession,
    *,
    user_id: str,
) -> dict[str, Any]:
    """Compara WR/avg_r entre in-sample y holdout. Si divergen mucho, el
    feedback loop está overfitteando.

    Retorna:
        {
          "in_sample": {"n": ..., "win_rate": ..., "avg_r": ...},
          "holdout":   {"n": ..., "win_rate": ..., "avg_r": ...},
          "delta_pp":  float,    # holdout_win_rate - in_sample_win_rate
        }

    No usa Bayesian aquí — queremos detectar drift a nivel de samples
    observadas, no estimar tasas subyacentes.
    """
    rows = (
        (
            await session.execute(
                text(
                    """
                SELECT
                    is_holdout,
                    COUNT(*) AS n,
                    COUNT(*) FILTER (WHERE r_multiple > 0.2) AS wins,
                    AVG(r_multiple) AS avg_r
                FROM journal_trades
                WHERE user_id = :uid
                  AND status = 'closed'
                  AND source = 'agent_proposal'
                  AND r_multiple IS NOT NULL
                GROUP BY is_holdout
                """
                ),
                {"uid": user_id},
            )
        )
        .mappings()
        .all()
    )

    in_sample = {"n": 0, "win_rate": None, "avg_r": None}
    holdout = {"n": 0, "win_rate": None, "avg_r": None}
    for r in rows:
        bucket = holdout if r["is_holdout"] else in_sample
        n = int(r["n"])
        wins = int(r["wins"])
        bucket["n"] = n
        bucket["win_rate"] = (wins / n) if n > 0 else None
        bucket["avg_r"] = float(r["avg_r"]) if r["avg_r"] is not None else None

    delta_pp: float | None = None
    if in_sample["win_rate"] is not None and holdout["win_rate"] is not None:
        delta_pp = (holdout["win_rate"] - in_sample["win_rate"]) * 100.0

    return {
        "in_sample": in_sample,
        "holdout": holdout,
        "delta_pp": delta_pp,
    }


# -----------------------------------------------------------------------------
# F5.5+: lecciones recientes asociadas a factores débiles
# -----------------------------------------------------------------------------


class LessonHit(BaseModel):
    """Lección extraída de un post-mortem reciente, asociada a un factor que
    falló (`factor_verdicts[factor_key].verdict = 'failed'`)."""

    factor_key: str  # "ema_stack@1h" o "lvn_support"
    lesson_es: str
    verdict: str  # thesis_broken normalmente; thesis_held no produce lesson útil para factor_key
    symbol: str
    regime_label: str | None
    created_at: datetime


async def get_recent_lessons_for_factors(
    session: AsyncSession,
    *,
    user_id: str,
    factor_keys: list[str],
    regime_label: str | None = None,
    lookback_days: int = 90,
    per_factor: int = 1,
) -> dict[str, list[LessonHit]]:
    """Devuelve, por cada `factor_key`, las `per_factor` lecciones más
    recientes de post-mortems donde ese factor aparece como 'failed' en
    `factor_verdicts`.

    `factor_key` formato: 'name@tf' para deterministic (ej. 'ema_stack@1h')
    o 'name' para semantic (ej. 'lvn_support'). Mismo shape que el
    vocabulario del post-mortem agent.

    Filtro `verdict='thesis_broken'`: solo lecciones de trades donde la
    tesis se rompió aportan señal de "evitar repetir". `thesis_held` no
    enseña qué evitar; `execution_error` y `noise` son ruido.
    """
    if not factor_keys:
        return {}

    out: dict[str, list[LessonHit]] = {key: [] for key in factor_keys}

    # Una query por factor_key — Postgres JSONB indexing no permite un solo
    # WHERE eficiente para "factor_verdicts contiene CUALQUIERA de N keys
    # con verdict='failed'". La alternativa (escanear todo y filtrar en
    # Python) es peor a escala. Cada query es indexable y barata.
    for key in factor_keys:
        params: dict[str, Any] = {
            "uid": user_id,
            "key": key,
            "lb": lookback_days,
            "per": per_factor,
        }
        regime_clause = ""
        if regime_label:
            regime_clause = (
                " AND (pm.factor_verdicts->'context'->>'regime_label' = :rl "
                "      OR jt.regime = :rl) "
            )
            params["rl"] = regime_label

        sql = f"""
            SELECT
                pm.lesson_es,
                pm.verdict,
                jt.symbol,
                jt.regime AS regime_label,
                pm.created_at
            FROM setup_post_mortems pm
            JOIN journal_trades jt ON jt.id = pm.trade_id
            WHERE pm.user_id = :uid
              AND pm.verdict = 'thesis_broken'
              AND pm.factor_verdicts ? :key
              AND pm.factor_verdicts -> :key ->> 'verdict' = 'failed'
              AND pm.created_at >= now() - make_interval(days => :lb)
              {regime_clause}
            ORDER BY pm.created_at DESC
            LIMIT :per
        """
        rows = (await session.execute(text(sql), params)).mappings().all()
        for r in rows:
            out[key].append(
                LessonHit(
                    factor_key=key,
                    lesson_es=str(r["lesson_es"]),
                    verdict=str(r["verdict"]),
                    symbol=str(r["symbol"]),
                    regime_label=r.get("regime_label"),
                    created_at=r["created_at"],
                )
            )
    return out


# -----------------------------------------------------------------------------
# F5.6 — Factor Gate (A.2 del plan integral)
# -----------------------------------------------------------------------------
#
# The agent can choose to lean on factors with documented poor historical
# win-rate (under the current user/regime). The auto-injected preamble
# surfaces these stats but is purely advisory — the agent can ignore them.
#
# The factor gate closes that loop. It walks the `factor_snapshot` the
# TradeIdea would persist, identifies the factors the setup is RELYING on,
# queries `get_factor_hit_rates` for the corresponding (user, regime) cell,
# and emits a `GateVerdict` with three buckets:
#
#   - advisory: n < 30 (insufficient evidence — no action, just visibility)
#   - soft_veto: 30 ≤ n < 100 AND wr_lcb < 35% (acceptable to keep the
#     trade but confidence is forced to 'low' and a warning is appended
#     to risk_notes)
#   - hard_veto: n ≥ 100 AND wr_lcb < 30% (validator raises ModelRetry —
#     the agent must drop this factor or pick another setup)
#
# The thresholds (sample size + LCB cutoffs) are deliberately conservative
# to keep false positives low while still binding once the data accumulates.


def _factor_kind_and_keys(
    factor_snapshot: dict[str, Any],
) -> list[tuple[str, str | None, str]]:
    """Extract the ``(factor_name, factor_tf, factor_kind)`` triples the
    setup is RELYING on, given the snapshot shape produced by
    ``validators._build_factor_snapshot``.

    Deterministic factors are present iff ``|value| >= PRESENT_THRESHOLD``;
    weaker contributions are noise — the agent isn't anchoring on them.
    Semantic tags are always included (their presence is binary).

    The returned shape matches the keys ``factor_outcomes`` uses, so the
    triples can be looked up directly via ``get_factor_hit_rates``.
    """
    out: list[tuple[str, str | None, str]] = []
    deterministic = factor_snapshot.get("deterministic", {})
    by_tf = deterministic.get("by_tf", {}) if isinstance(deterministic, dict) else {}
    if isinstance(by_tf, dict):
        for tf, factors in by_tf.items():
            if not isinstance(tf, str) or not isinstance(factors, dict):
                continue
            for fname, fval in factors.items():
                if fname == "score_total":
                    continue
                if isinstance(fval, bool):
                    continue
                if not isinstance(fval, (int, float)):
                    continue
                if abs(float(fval)) >= PRESENT_THRESHOLD:
                    out.append((fname, tf, "deterministic"))
    semantic_tags = factor_snapshot.get("semantic_tags", [])
    if isinstance(semantic_tags, list):
        for tag in semantic_tags:
            if isinstance(tag, str) and tag:
                out.append((tag, None, "semantic"))
    return out


def _apply_gate_to_rates(
    triples: list[tuple[str, str | None, str]],
    rates: list[FactorHitRate],
) -> GateVerdict:
    """Pure decisional core of the factor gate. Separated from DB I/O so
    the policy can be unit-tested without a database."""
    if not triples:
        return GateVerdict(passed=True)

    by_key: dict[tuple[str, str | None, str], FactorHitRate] = {
        (r.factor_name, r.factor_tf, r.factor_kind): r for r in rates
    }

    blocking: list[FactorBlock] = []
    soft: list[FactorBlock] = []
    advisory: list[FactorBlock] = []

    for fname, ftf, fkind in triples:
        hit = by_key.get((fname, ftf, fkind))
        if hit is None:
            # No prior outcomes for this exact (factor, tf, kind) cell —
            # advisory only. The agent should know the factor is unseen.
            advisory.append(
                FactorBlock(
                    factor_name=fname,
                    factor_tf=ftf,
                    factor_kind=fkind,
                    n_trades=0,
                    win_rate_lcb=0.0,
                    severity="advisory",
                )
            )
            continue
        n = hit.n_trades
        lcb = hit.win_rate_lcb
        if n >= FACTOR_GATE_HARD_MIN_N and lcb < FACTOR_GATE_HARD_LCB_THRESHOLD:
            blocking.append(
                FactorBlock(
                    factor_name=fname,
                    factor_tf=ftf,
                    factor_kind=fkind,
                    n_trades=n,
                    win_rate_lcb=lcb,
                    severity="hard_veto",
                )
            )
        elif (
            FACTOR_GATE_SOFT_MIN_N <= n < FACTOR_GATE_HARD_MIN_N
            and lcb < FACTOR_GATE_SOFT_LCB_THRESHOLD
        ):
            soft.append(
                FactorBlock(
                    factor_name=fname,
                    factor_tf=ftf,
                    factor_kind=fkind,
                    n_trades=n,
                    win_rate_lcb=lcb,
                    severity="soft_veto",
                )
            )
        elif n < FACTOR_GATE_SOFT_MIN_N:
            advisory.append(
                FactorBlock(
                    factor_name=fname,
                    factor_tf=ftf,
                    factor_kind=fkind,
                    n_trades=n,
                    win_rate_lcb=lcb,
                    severity="advisory",
                )
            )
        # else: factor has enough samples AND lcb is healthy — no entry needed.

    return GateVerdict(
        passed=len(blocking) == 0,
        blocking_factors=blocking,
        soft_veto_factors=soft,
        advisory_factors=advisory,
    )


async def evaluate_factor_gate(
    session: AsyncSession,
    *,
    user_id: str,
    factor_snapshot: dict[str, Any],
    regime_label: str | None = None,
    lookback_days: int | None = None,
) -> GateVerdict:
    """Run the factor gate on the candidate TradeIdea's snapshot.

    Returns a ``GateVerdict`` the validator can inspect to decide between
    accept-as-is, downgrade-confidence, or ModelRetry. DB failures should
    be handled by the caller (we don't swallow them here — let the
    validator decide whether transient infra issues should block a trade).
    """
    triples = _factor_kind_and_keys(factor_snapshot)
    if not triples:
        return GateVerdict(passed=True)

    factor_names = sorted({fname for fname, _, _ in triples})
    rates = await get_factor_hit_rates(
        session,
        user_id=user_id,
        factors=factor_names,
        regime_label=regime_label,
        lookback_days=lookback_days,
        include_holdout=False,
    )
    return _apply_gate_to_rates(triples, rates)


__all__ = [
    "FACTOR_GATE_HARD_LCB_THRESHOLD",
    "FACTOR_GATE_HARD_MIN_N",
    "FACTOR_GATE_SOFT_LCB_THRESHOLD",
    "FACTOR_GATE_SOFT_MIN_N",
    "PRESENT_THRESHOLD",
    "PRIOR_ALPHA",
    "PRIOR_BETA",
    "CombinedStat",
    "FactorHitRate",
    "LessonHit",
    "_apply_gate_to_rates",
    "_factor_kind_and_keys",
    "_posterior_stats",
    "evaluate_factor_gate",
    "get_combined_hit_rate",
    "get_factor_hit_rates",
    "get_holdout_performance_summary",
    "get_recent_lessons_for_factors",
    "get_top_factors_for_preamble",
]
