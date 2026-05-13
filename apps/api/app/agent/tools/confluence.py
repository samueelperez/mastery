"""get_multi_tf_confluence — per-TF bias multifactor.

V2 (post-auditoría 2026-05): el score ya no es sólo EMA-stack. Combina
cinco factores ortogonales con pesos calibrados para reflejar lo que un
trader pro mira en cripto:

    factor               peso    señal
    ─────────────────────────────────────────────────────────────────────
    EMA stack 21/55/200  0.30    alineamiento clásico de medias
    ADX / regime         0.20    fuerza del trend (ADX>25 = trend real)
    RSI extremo          0.15    sobrecompra/sobreventa
    Volumen relativo     0.15    confirmación con dinero detrás
    Distancia EMA21/ATR  0.20    extensión vs media corta normalizada

Cada factor produce un valor en [-1, +1]. El score total es el weighted-sum,
también en [-1, +1]. `bias` se mapea: > +0.4 = bull, < -0.4 = bear, else range.

Backward compat: mantenemos `score: int` como round(score_total · 3) ∈ [-3, 3]
para no romper consumidores existentes. Las componentes detalladas están en
`score_components` (nuevo campo).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.deps import AgentDeps
from app.agent.tools._envelope import Provenance, ToolResult
from app.agent.tools._time import floor_to_timeframe, staleness_warning
from app.market.indicators import IndicatorSpec, compute_panel

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


# Pesos del score multifactor. Suma = 1.0.
WEIGHTS = {
    "ema_stack": 0.30,
    "regime": 0.20,
    "rsi": 0.15,
    "volume": 0.15,
    "distance_atr": 0.20,
}


class ScoreComponents(BaseModel):
    """Desglose del score por factor — útil para que el LLM cite por qué
    el bias es lo que es y para auditar setups con bias dudoso."""

    ema_stack: float = Field(..., ge=-1.0, le=1.0)
    regime: float = Field(..., ge=-1.0, le=1.0)
    rsi: float = Field(..., ge=-1.0, le=1.0)
    volume: float = Field(..., ge=-1.0, le=1.0)
    distance_atr: float = Field(..., ge=-1.0, le=1.0)


class TimeframeBias(BaseModel):
    timeframe: Literal["15m", "1h", "4h", "1d"]
    bias: Literal["bull", "bear", "range"]
    # Score legacy en [-3, +3] = round(score_total · 3). Mantenido por
    # compatibilidad — consumidores nuevos deberían usar `score_components`.
    score: int = Field(..., ge=-3, le=3)
    # Score continuo en [-1, +1] tras pesar los componentes.
    score_total: float = Field(..., ge=-1.0, le=1.0)
    score_components: ScoreComponents
    reasons: list[str] = Field(default_factory=list)
    last_close: float | None = None
    ema_21: float | None = None
    ema_55: float | None = None
    ema_200: float | None = None
    rsi_14: float | None = None
    adx_14: float | None = None


class ConfluenceMap(BaseModel):
    by_tf: list[TimeframeBias]
    aggregate_bias: Literal["bull", "bear", "range"]
    aggregate_agreement_pct: float = Field(..., ge=0.0, le=100.0)


def _factor_ema_stack(
    close: float | None,
    ema21: float | None,
    ema55: float | None,
    ema200: float | None,
) -> tuple[float, list[str]]:
    """3 condiciones binarias normalizadas a [-1, +1]: ema21 vs ema55,
    ema55 vs ema200, close vs ema21. Cada una +1 o -1, promedio = score."""
    parts: list[float] = []
    reasons: list[str] = []
    if ema21 is not None and ema55 is not None:
        if ema21 > ema55:
            parts.append(1.0)
            reasons.append("EMA21>EMA55")
        elif ema21 < ema55:
            parts.append(-1.0)
            reasons.append("EMA21<EMA55")
    if ema55 is not None and ema200 is not None:
        if ema55 > ema200:
            parts.append(1.0)
            reasons.append("EMA55>EMA200")
        elif ema55 < ema200:
            parts.append(-1.0)
            reasons.append("EMA55<EMA200")
    if close is not None and ema21 is not None:
        if close > ema21:
            parts.append(1.0)
            reasons.append("close>EMA21")
        elif close < ema21:
            parts.append(-1.0)
            reasons.append("close<EMA21")
    if not parts:
        return 0.0, []
    return sum(parts) / len(parts), reasons


def _factor_regime(adx: float | None, ema_dir: float) -> tuple[float, list[str]]:
    """ADX>25 confirma que hay trend real. Direccionalidad la toma del
    EMA-stack (ema_dir signo). ADX<20 = chop, signo del componente = 0."""
    if adx is None:
        return 0.0, []
    if adx >= 25:
        sign = 1.0 if ema_dir > 0 else -1.0 if ema_dir < 0 else 0.0
        return sign, [f"ADX {adx:.0f} (trend)"]
    if adx >= 20:
        sign = 0.5 if ema_dir > 0 else -0.5 if ema_dir < 0 else 0.0
        return sign, [f"ADX {adx:.0f} (trend débil)"]
    return 0.0, [f"ADX {adx:.0f} (chop)"]


def _factor_rsi(rsi: float | None) -> tuple[float, list[str]]:
    """RSI > 70 = sobrecompra (bajista para mean-reversion); 60-70 = momentum
    alcista. Inverso a la baja. Mapeo lineal: rsi=50 → 0, rsi=70 → +0.5,
    rsi=80 → +1, rsi=30 → -0.5, rsi=20 → -1."""
    if rsi is None:
        return 0.0, []
    # Neutral en 50, ±1 en 30/70 (fuerza máxima del momentum).
    deviation = (rsi - 50) / 20.0  # rsi=70 → +1.0, rsi=30 → -1.0
    score = max(-1.0, min(1.0, deviation))
    if rsi >= 70:
        return score, [f"RSI {rsi:.0f} sobrecompra"]
    if rsi <= 30:
        return score, [f"RSI {rsi:.0f} sobreventa"]
    if rsi >= 60:
        return score, [f"RSI {rsi:.0f} momentum"]
    if rsi <= 40:
        return score, [f"RSI {rsi:.0f} debilidad"]
    return 0.0, [f"RSI {rsi:.0f} neutro"]


def _factor_volume(
    last_volume: float | None,
    avg_volume_20: float | None,
    direction: float,
) -> tuple[float, list[str]]:
    """Volumen actual vs media móvil 20. Si > 1.3× la media Y la dirección
    del precio coincide, suma fuerza. Si > 1.3× contra la dirección, resta
    (volumen vendedor en alcista, alcista en bajista)."""
    if last_volume is None or avg_volume_20 is None or avg_volume_20 <= 0:
        return 0.0, []
    ratio = last_volume / avg_volume_20
    if ratio < 1.3:
        return 0.0, [f"vol normal (×{ratio:.1f})"]
    # Volumen alto: confirma dirección.
    sign = 1.0 if direction > 0 else -1.0 if direction < 0 else 0.0
    if sign == 0:
        return 0.0, [f"vol alto ×{ratio:.1f} sin dir"]
    return sign, [f"vol ×{ratio:.1f} confirma {'alza' if sign > 0 else 'baja'}"]


def _factor_distance_atr(
    close: float | None,
    ema21: float | None,
    atr: float | None,
) -> tuple[float, list[str]]:
    """Distancia normalizada close↔EMA21 en unidades ATR. >1 ATR arriba =
    extension alcista; <-1 ATR abajo = extension bajista. Cap en ±2.5."""
    if close is None or ema21 is None or atr is None or atr <= 0:
        return 0.0, []
    distance = (close - ema21) / atr
    score = max(-1.0, min(1.0, distance / 2.5))
    abs_dist = abs(distance)
    if abs_dist >= 1.5:
        return score, [f"close a {distance:+.1f} ATR de EMA21 (extendido)"]
    if abs_dist >= 0.7:
        return score, [f"close a {distance:+.1f} ATR de EMA21"]
    return 0.0, [f"close pegado a EMA21 ({distance:+.1f} ATR)"]


async def _bias_for_tf(
    *,
    session_factory: SessionFactory,
    exchange: str,
    symbol: str,
    tf: str,
    until: datetime | None = None,
) -> tuple[TimeframeBias, datetime, list[str]]:
    """Calcula el bias multifactor para un (symbol, tf).

    `until`: si None, usa floor_to_timeframe(now, tf) (= próxima vela
    cerrada). Si se pasa, calcula el bias en ese instante histórico —
    permite reproducir el ScoreComponents que existía cuando un trade
    fue propuesto o cuando cerró (entry_vs_exit_delta del post-mortem,
    backfill retroactivo).
    """
    if until is None:
        cutoff = floor_to_timeframe(datetime.now(tz=UTC), tf)
    else:
        cutoff = floor_to_timeframe(until, tf)
    async with session_factory() as session:
        df = await compute_panel(
            session,
            exchange=exchange,
            symbol=symbol,
            timeframe=tf,
            lookback=300,
            specs=[
                IndicatorSpec(name="ema", length=21),
                IndicatorSpec(name="ema", length=55),
                IndicatorSpec(name="ema", length=200),
                IndicatorSpec(name="rsi", length=14),
                IndicatorSpec(name="atr", length=14),
                IndicatorSpec(name="adx", length=14),
                IndicatorSpec(name="sma", length=20),  # para volumen — mean(volume) lo
                # haríamos directo, pero no tenemos un indicator volume_sma; usamos
                # una columna calculada manualmente abajo.
            ],
            until=cutoff,
        )
    if df.height == 0:
        return (
            TimeframeBias(
                timeframe=tf, bias="range", score=0, score_total=0.0,
                score_components=ScoreComponents(
                    ema_stack=0.0, regime=0.0, rsi=0.0, volume=0.0, distance_atr=0.0
                ),
                reasons=["sin velas"],
            ),
            cutoff,
            ["no candles in lookback window"],
        )

    last = df.tail(1).to_dicts()[0]
    close = last["c"]
    ema21 = last.get("ema_21")
    ema55 = last.get("ema_55")
    ema200 = last.get("ema_200")
    rsi_14 = last.get("rsi_14")
    atr_14 = last.get("atr_14")
    adx_14 = last.get("adx")  # default name del adx tool

    # Volumen relativo: media de los últimos 20 vs último.
    last_volume = last["v"]
    if df.height >= 20:
        recent_vol = df.tail(20)["v"].mean()
    else:
        recent_vol = None

    # === Calcular cada componente en [-1, +1] ===
    f_ema, r_ema = _factor_ema_stack(close, ema21, ema55, ema200)
    f_regime, r_regime = _factor_regime(adx_14, f_ema)
    f_rsi, r_rsi = _factor_rsi(rsi_14)
    f_vol, r_vol = _factor_volume(last_volume, recent_vol, f_ema)
    f_dist, r_dist = _factor_distance_atr(close, ema21, atr_14)

    score_total = (
        WEIGHTS["ema_stack"] * f_ema
        + WEIGHTS["regime"] * f_regime
        + WEIGHTS["rsi"] * f_rsi
        + WEIGHTS["volume"] * f_vol
        + WEIGHTS["distance_atr"] * f_dist
    )
    # Cap defensivo (los componentes ya son [-1,+1] y los pesos suman 1).
    score_total = max(-1.0, min(1.0, score_total))

    bias: Literal["bull", "bear", "range"]
    if score_total >= 0.4:
        bias = "bull"
    elif score_total <= -0.4:
        bias = "bear"
    else:
        bias = "range"

    reasons = r_ema + r_regime + r_rsi + r_vol + r_dist

    last_ts = last["ts"]
    warnings: list[str] = []
    if w := staleness_warning(last_closed=last_ts, timeframe=tf):
        warnings.append(w)

    return (
        TimeframeBias(
            timeframe=tf,
            bias=bias,
            # Legacy score: redondeo del total al rango -3..+3.
            score=int(round(score_total * 3)),
            score_total=round(score_total, 3),
            score_components=ScoreComponents(
                ema_stack=round(f_ema, 3),
                regime=round(f_regime, 3),
                rsi=round(f_rsi, 3),
                volume=round(f_vol, 3),
                distance_atr=round(f_dist, 3),
            ),
            reasons=reasons,
            last_close=float(close),
            ema_21=float(ema21) if ema21 is not None else None,
            ema_55=float(ema55) if ema55 is not None else None,
            ema_200=float(ema200) if ema200 is not None else None,
            rsi_14=float(rsi_14) if rsi_14 is not None else None,
            adx_14=float(adx_14) if adx_14 is not None else None,
        ),
        last_ts,
        warnings,
    )


def register_confluence_tools(agent: Agent[AgentDeps, object]) -> None:
    @agent.tool
    async def get_multi_tf_confluence(
        ctx: RunContext[AgentDeps],
        symbol: str,
        timeframes: Annotated[
            list[Literal["15m", "1h", "4h", "1d"]] | None,
            Field(min_length=1, max_length=4),
        ] = None,
    ) -> ToolResult[ConfluenceMap]:
        """For each timeframe, compute the bias from a multifactor score:
        EMA stack (0.30) + ADX/regime (0.20) + RSI extremo (0.15) + volumen
        relativo (0.15) + distancia close-EMA21 en ATR (0.20).

        bias: total >= +0.4 → bull. <= -0.4 → bear. Otherwise range.

        El score per-TF es continuo en [-1, +1] (`score_total`) y desglosado
        por componente en `score_components`. Cita el componente específico
        cuando justifiques una claim (ej. "RSI 75 en sobrecompra y close a
        +1.8 ATR de EMA21" — no sólo "EMA stack alcista").
        """
        symbol = symbol.upper()
        if timeframes is None:
            timeframes = ["15m", "1h", "4h", "1d"]
        results = await asyncio.gather(
            *[
                _bias_for_tf(
                    session_factory=ctx.deps.session_factory,
                    exchange=ctx.deps.exchange,
                    symbol=symbol,
                    tf=tf,
                )
                for tf in timeframes
            ]
        )
        biases = [r[0] for r in results]
        last_ts = max(r[1] for r in results)
        all_warnings = [w for r in results for w in r[2]]

        bull = sum(1 for b in biases if b.bias == "bull")
        bear = sum(1 for b in biases if b.bias == "bear")
        if bull > bear:
            agg = "bull"
            agreement = 100.0 * bull / len(biases)
        elif bear > bull:
            agg = "bear"
            agreement = 100.0 * bear / len(biases)
        else:
            agg = "range"
            agreement = 100.0 * (len(biases) - bull - bear) / len(biases)

        ctx.deps.log.info(
            "tool.get_multi_tf_confluence",
            symbol=symbol,
            n_timeframes=len(timeframes),
            aggregate=agg,
            v="2",
        )

        return ToolResult(
            data=ConfluenceMap(
                by_tf=biases,
                aggregate_bias=agg,
                aggregate_agreement_pct=agreement,
            ),
            provenance=Provenance(
                source=f"db.ohlcv:{ctx.deps.exchange}:{symbol}:multi-tf",
                as_of=last_ts,
                rows=sum(1 for _ in biases),
                warnings=all_warnings,
            ),
        )


# ============================================================================
# Helpers públicos reusables (F5.5 post-mortem)
# ============================================================================


async def compute_score_components(
    *,
    session_factory: SessionFactory,
    exchange: str,
    symbol: str,
    timeframes: list[str] | None = None,
    until: datetime | None = None,
) -> ConfluenceMap:
    """Reusable de `get_multi_tf_confluence` sin envelope ni RunContext.

    Llamado desde el validator (para construir `factor_snapshot` al crear
    el setup), desde el post_mortem_dispatcher (para `entry_vs_exit_delta`)
    y desde el script de backfill. NO se registra como tool — el agente
    debe seguir llamando `get_multi_tf_confluence` con su envelope, no este
    helper crudo.
    """
    symbol = symbol.upper()
    if timeframes is None:
        timeframes = ["15m", "1h", "4h", "1d"]
    results = await asyncio.gather(
        *[
            _bias_for_tf(
                session_factory=session_factory,
                exchange=exchange,
                symbol=symbol,
                tf=tf,
                until=until,
            )
            for tf in timeframes
        ]
    )
    biases = [r[0] for r in results]
    bull = sum(1 for b in biases if b.bias == "bull")
    bear = sum(1 for b in biases if b.bias == "bear")
    if bull > bear:
        agg = "bull"
        agreement = 100.0 * bull / len(biases)
    elif bear > bull:
        agg = "bear"
        agreement = 100.0 * bear / len(biases)
    else:
        agg = "range"
        agreement = 100.0 * (len(biases) - bull - bear) / len(biases)
    return ConfluenceMap(
        by_tf=biases, aggregate_bias=agg, aggregate_agreement_pct=agreement
    )


def confluence_map_to_factor_snapshot_deterministic(
    cmap: ConfluenceMap,
) -> dict[str, object]:
    """Empaqueta ConfluenceMap al shape esperado por `journal_trades.factor_
    snapshot.deterministic`. Cada factor_name del scorer (ema_stack, regime,
    rsi, volume, distance_atr) se persiste por timeframe como valor float;
    el `score_total` también va incluido (skip en fan-out a factor_outcomes).
    """
    by_tf: dict[str, dict[str, float]] = {}
    for b in cmap.by_tf:
        by_tf[b.timeframe] = {
            "ema_stack": b.score_components.ema_stack,
            "regime": b.score_components.regime,
            "rsi": b.score_components.rsi,
            "volume": b.score_components.volume,
            "distance_atr": b.score_components.distance_atr,
            "score_total": b.score_total,
        }
    return {
        "by_tf": by_tf,
        "aggregate_bias": cmap.aggregate_bias,
        "aggregate_agreement_pct": cmap.aggregate_agreement_pct,
    }
