"""get_factor_hit_rates tool — win-rate Bayesian por factor histórico.

Para el feedback loop F5.5. El agente principal puede llamar esta tool al
proponer un setup para verificar "¿qué factores funcionan mejor en mi
histórico bajo este régimen?". También está disponible para el
post_mortem_agent (contexto del histórico al juzgar el cierre).

El auto-inject preamble en `chat.py` ya entrega los top factores en cada
turno — esta tool sirve para deep-dives filtrados (por símbolo, régimen,
side, factor específico).
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from app.agent.deps import AgentDeps
from app.agent.tools._envelope import Provenance, ToolResult
from app.backtest.factor_stats_repo import (
    PRIOR_ALPHA,
    PRIOR_BETA,
)
from app.backtest.factor_stats_repo import (
    get_factor_hit_rates as _repo_get_factor_hit_rates,
)
from app.backtest.factor_stats_repo import (
    get_recent_lessons_for_factors as _repo_get_recent_lessons_for_factors,
)

# Umbral: solo adjuntamos `recent_lesson` para factores con WR_lcb por debajo
# de este corte Y con muestra suficiente. Filtros más estrictos evitan
# inundar el output del agente con lecciones de factores ruidosos.
WEAK_FACTOR_LCB_THRESHOLD = 0.30
WEAK_FACTOR_MIN_N = 5


class FactorHitRateRow(BaseModel):
    """Compact view de FactorHitRate para JSON output del tool.

    Mismo contenido que la fila del repo pero con campos redondeados y
    nombres cortos para reducir input tokens en el siguiente turno del agente.
    """

    name: str = Field(..., description="Factor name (e.g. 'ema_stack', 'rsi').")
    tf: str | None = Field(default=None, description="Factor timeframe (or null for semantic).")
    kind: Literal["deterministic", "semantic"]
    n: int = Field(..., description="Trades cerrados con este factor presente.")
    wins: int = Field(..., description="Wins (r_multiple > 0.2).")
    wr_mean: float = Field(..., description="Posterior expected win-rate [0,1].")
    wr_lcb: float = Field(
        ...,
        description=(
            "5th percentile del posterior — el 'trust floor'. Decisión = "
            "usa este valor, no wr_mean. Penaliza naturalmente n pequeño."
        ),
    )
    wr_ucb: float = Field(..., description="95th percentile del posterior.")
    avg_r: float | None
    expectancy_r: float | None
    # F5.5: lección textual extraída del último post-mortem donde este
    # factor falló (verdict='thesis_broken' + factor_verdicts[key].verdict
    # = 'failed'). Solo poblado para factores con wr_lcb<0.30 y n>=5.
    # Permite al agente principal aprender el "por qué" cualitativo, no
    # solo el número.
    recent_lesson: str | None = None
    recent_lesson_symbol: str | None = None


class FactorHitRatesOut(BaseModel):
    rows: list[FactorHitRateRow]
    prior: dict[str, float] = Field(
        default_factory=lambda: {"alpha": PRIOR_ALPHA, "beta": PRIOR_BETA}
    )
    filters_applied: dict[str, object] = Field(default_factory=dict)
    holdout_excluded: bool = True


def _stable_run_id(
    *,
    user_id: str,
    factors: tuple[str, ...] | None,
    symbol: str | None,
    regime: str | None,
    side: str | None,
    lookback: int | None,
) -> str:
    """Handle determinista para citaciones. El validator main agent puede
    walking-check este `run_id` igual que hace con tools como run_backtest
    (ver `validators._walk_handles`)."""
    parts = [
        user_id,
        ",".join(sorted(factors)) if factors else "*",
        symbol or "*",
        regime or "*",
        side or "*",
        str(lookback) if lookback is not None else "*",
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def register_factor_stats_tool(agent: Agent[AgentDeps, object]) -> None:
    @agent.tool
    async def get_factor_hit_rates(
        ctx: RunContext[AgentDeps],
        factors: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Lista de factor_name (ej. ['ema_stack', 'rsi', 'volume', "
                    "'lvn_support']). None = todos los factores con datos. "
                    "Usa esto para verificar específicamente los factores que "
                    "vas a citar en el TradeIdea."
                )
            ),
        ] = None,
        symbol: Annotated[
            str | None,
            Field(description="Filtrar a un símbolo (ej. 'BTCUSDT'). None = todos."),
        ] = None,
        regime: Annotated[
            Literal["trending_up", "trending_down", "ranging", "volatile_expansion"] | None,
            Field(description="Filtrar al régimen de mercado actual."),
        ] = None,
        timeframe: Annotated[
            Literal["15m", "1h", "4h", "1d"] | None,
            Field(description="Timeframe del trade (no del factor)."),
        ] = None,
        lookback_days: Annotated[
            int | None,
            Field(ge=7, le=730, description="Ventana en días. None = todo el histórico."),
        ] = 180,
        factor_kind: Annotated[
            Literal["deterministic", "semantic"] | None,
            Field(description="Filtrar solo factores deterministic (del scorer) o semantic (tags)."),
        ] = None,
    ) -> ToolResult[FactorHitRatesOut]:
        """Hit-rate Bayesian (Beta-Binomial posterior) por factor del histórico
        del usuario.

        IMPORTANTE: usa `wr_lcb` (lower credibility bound, 5th percentile) para
        decisiones — no `wr_mean`. Con `n` pequeño el posterior es ancho y el
        lcb cae mucho; el sistema te está diciendo "no hay suficiente
        evidencia". Política: no confíes en un factor con `wr_lcb < 50%`.

        Holdout excluido por defecto: las stats reflejan SOLO los trades
        in-sample (~85% del histórico). El 15% holdout queda intocado para
        validar que el feedback loop no overfitea su propio histórico.

        Returns:
            FactorHitRatesOut con filas ordenadas por `n` descendente (los
            factores con más muestra primero).
        """
        async with ctx.deps.session_factory() as session:
            rows = await _repo_get_factor_hit_rates(
                session,
                user_id=ctx.deps.user_id,
                factors=factors,
                symbol=symbol,
                timeframe=timeframe,
                regime_label=regime,
                lookback_days=lookback_days,
                factor_kind=factor_kind,
                include_holdout=False,
            )

            # F5.5: para factores débiles (wr_lcb<threshold con n>=min),
            # fetch la lección más reciente del histórico de post-mortems.
            # Anchor key: 'name@tf' si factor_tf existe; 'name' si no
            # (semantic). Mismo vocabulario que `factor_verdicts` en
            # post_mortem_dispatcher._build_factor_verdicts().
            weak_keys = [
                f"{r.factor_name}@{r.factor_tf}" if r.factor_tf else r.factor_name
                for r in rows
                if r.win_rate_lcb < WEAK_FACTOR_LCB_THRESHOLD
                and r.n_trades >= WEAK_FACTOR_MIN_N
            ]
            lessons_by_key: dict[str, list] = {}
            if weak_keys:
                lessons_by_key = await _repo_get_recent_lessons_for_factors(
                    session,
                    user_id=ctx.deps.user_id,
                    factor_keys=weak_keys,
                    regime_label=regime,
                    lookback_days=lookback_days or 180,
                    per_factor=1,
                )

        def _lesson_for(r) -> tuple[str | None, str | None]:
            key = f"{r.factor_name}@{r.factor_tf}" if r.factor_tf else r.factor_name
            hits = lessons_by_key.get(key) or []
            if not hits:
                return None, None
            top = hits[0]
            return top.lesson_es, top.symbol

        compact = []
        for r in rows:
            lesson_text, lesson_symbol = _lesson_for(r)
            compact.append(
                FactorHitRateRow(
                    name=r.factor_name,
                    tf=r.factor_tf,
                    kind=r.factor_kind,
                    n=r.n_trades,
                    wins=r.n_wins,
                    wr_mean=round(r.win_rate_mean, 3),
                    wr_lcb=round(r.win_rate_lcb, 3),
                    wr_ucb=round(r.win_rate_ucb, 3),
                    avg_r=round(r.avg_r, 3) if r.avg_r is not None else None,
                    expectancy_r=(
                        round(r.expectancy_r, 3) if r.expectancy_r is not None else None
                    ),
                    recent_lesson=lesson_text,
                    recent_lesson_symbol=lesson_symbol,
                )
            )

        as_of_dt = max((r.last_closed_at for r in rows if r.last_closed_at), default=None)
        run_id = _stable_run_id(
            user_id=ctx.deps.user_id,
            factors=tuple(factors) if factors else None,
            symbol=symbol,
            regime=regime,
            side=None,
            lookback=lookback_days,
        )

        ctx.deps.log.info(
            "tool.get_factor_hit_rates",
            n_factors=len(compact),
            symbol=symbol,
            regime=regime,
            lookback_days=lookback_days,
        )

        return ToolResult(
            data=FactorHitRatesOut(
                rows=compact,
                filters_applied={
                    "factors": factors,
                    "symbol": symbol,
                    "regime": regime,
                    "timeframe": timeframe,
                    "lookback_days": lookback_days,
                    "factor_kind": factor_kind,
                    "run_id": run_id,
                },
                holdout_excluded=True,
            ),
            provenance=Provenance(
                source=f"db.factor_outcomes:{ctx.deps.user_id}:bayesian",
                as_of=as_of_dt or datetime.fromtimestamp(0),
                rows=len(compact),
                warnings=(
                    [] if compact else [
                        "no factor data yet — close some setups first or check "
                        "factor_snapshot is being persisted"
                    ]
                ),
            ),
        )
