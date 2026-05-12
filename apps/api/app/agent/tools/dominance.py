"""get_market_dominance tool — BTC.D / Total3 share + multi-day trends + regime.

En cripto, una porción enorme del flujo entre activos se determina por la
rotación macro-cripto: cuando capital fluye DESDE alts HACIA BTC, todo bias
técnico en alts está peleando esa rotación (y suele perder). Esta tool
materializa ese contexto que el agente no podía ver con indicadores aislados.

Régimen derivado del nivel BTC.D + dirección reciente:
- btc_season — alts sangrando hacia BTC.
- alt_season — capital rotando A alts.
- mixed — equilibrio o señales conflictivas.
- range — dominance lateral, sin rotación clara.

Datos: CoinGecko /api/v3/global cacheado 15min en Redis. History 24h/7d se
acumula con uso del sistema; primer call tras gap largo reporta trends
indeterminados con warning.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

from app.agent.deps import AgentDeps
from app.agent.tools._envelope import Provenance, ToolResult
from app.dominance.provider import (
    DominanceTrend,
    RegimeLabel,
    classify_regime,
    classify_trend,
    get_dominance_history,
    get_dominance_snapshot,
)


class MarketDominanceOut(BaseModel):
    btc_dominance_pct: float
    eth_dominance_pct: float
    total3_share_pct: float  # (100 - btc - eth)
    total_market_cap_usd: float
    btc_dominance_trend_24h: DominanceTrend
    btc_dominance_trend_7d: DominanceTrend
    regime: RegimeLabel
    interpretation: str


def _interpret(
    *,
    btc_dom: float,
    eth_dom: float,
    total3: float,
    regime: RegimeLabel,
    trend_24h: DominanceTrend,
    trend_7d: DominanceTrend,
) -> str:
    if regime == "btc_season":
        return (
            f"BTC.D {btc_dom:.1f}% con tendencia 7d {trend_7d.delta_pct:+.1f}pp. "
            f"Capital rotando A BTC — alts probablemente sangrarán contra USDT "
            f"y contra BTC. Bias en alt operado contra-régimen tiene drawdown "
            f"asimétrico; pide confirmación adicional o reduce sizing."
        )
    if regime == "alt_season":
        return (
            f"BTC.D {btc_dom:.1f}% con tendencia 7d {trend_7d.delta_pct:+.1f}pp. "
            f"Capital rotando A alts (share fuera de BTC+ETH: {total3:.1f}%). "
            f"Bias bullish en mid-caps tiene viento de cola; bias bearish "
            f"rema contra el flujo."
        )
    if regime == "range":
        return (
            f"BTC.D {btc_dom:.1f}% lateral (7d {trend_7d.delta_pct:+.1f}pp). "
            f"Sin rotación macro-cripto clara — el bias técnico del símbolo "
            f"manda; no esperes tail wind ni head wind del régimen."
        )
    return (
        f"BTC.D {btc_dom:.1f}% con señales mixtas (24h {trend_24h.delta_pct:+.1f}pp, "
        f"7d {trend_7d.delta_pct:+.1f}pp). Régimen indefinido — trata bias "
        f"como independiente con sizing conservador."
    )


def register_dominance_tool(agent: Agent[AgentDeps, object]) -> None:
    @agent.tool
    async def get_market_dominance(
        ctx: RunContext[AgentDeps],
    ) -> ToolResult[MarketDominanceOut]:
        """Snapshot de dominance macro-cripto: BTC.D, ETH.D, share fuera de
        BTC+ETH, trends 24h/7d y régimen derivado.

        Crucial para alts. Si propones bias bullish en ETHUSDT mientras BTC.D
        sube fuerte (btc_season), el setup tiene viento en contra estructural
        que ningún indicador técnico aislado del símbolo refleja. Usa el
        `regime` como modulador de confidence y como input para
        `confluences` cuando operes alts.

        Para BTCUSDT sigue siendo útil: btc_season implica fortaleza
        relativa contra alts; range implica que BTC tampoco está liderando.

        Sin parámetros — el snapshot es global. Datos de CoinGecko, cached
        15min. La history 24h/7d se acumula con uso; un call sin history
        suficiente reporta `direction='indeterminate'` con warning en
        provenance (no es un error — solo aún no hay base para el delta).
        """
        # CoinGecko/Redis are external dependencies the chat must not crash on.
        # 429s and timeouts are rare but happen — degrade gracefully to a
        # provenance warning so the agent sees "unavailable" and routes around.
        now = datetime.now(tz=UTC)
        upstream_error: str | None = None
        snap = None
        try:
            snap = await get_dominance_snapshot()
        except Exception as exc:
            upstream_error = f"{type(exc).__name__}: {str(exc)[:120]}"
            ctx.deps.log.warning(
                "tool.get_market_dominance.fetch_failed",
                error=upstream_error,
            )

        if snap is None:
            return ToolResult(
                data=MarketDominanceOut(
                    btc_dominance_pct=0.0,
                    eth_dominance_pct=0.0,
                    total3_share_pct=0.0,
                    total_market_cap_usd=0.0,
                    btc_dominance_trend_24h=DominanceTrend(
                        direction="indeterminate", delta_pct=0.0
                    ),
                    btc_dominance_trend_7d=DominanceTrend(
                        direction="indeterminate", delta_pct=0.0
                    ),
                    regime="range",
                    interpretation=(
                        "Dominance no disponible — fuente externa (CoinGecko) "
                        "respondió error. Trata el régimen como desconocido en "
                        "este turno; no anuncies un dato que no tienes."
                    ),
                ),
                provenance=Provenance(
                    source="external.coingecko:global",
                    as_of=now,
                    rows=0,
                    warnings=[
                        f"upstream_unavailable: {upstream_error or 'unknown error'}",
                    ],
                ),
            )

        # Wider windows for the 7d lookup: snapshots only get persisted when
        # someone calls the tool, so we need slack to find a usable prior.
        prior_24h = await get_dominance_history(now - timedelta(hours=24), window_hours=6)
        prior_7d = await get_dominance_history(now - timedelta(days=7), window_hours=18)

        trend_24h = classify_trend(
            snap.btc_dominance_pct,
            prior_24h.btc_dominance_pct if prior_24h else None,
        )
        trend_7d = classify_trend(
            snap.btc_dominance_pct,
            prior_7d.btc_dominance_pct if prior_7d else None,
        )

        regime = classify_regime(
            btc_dominance_pct=snap.btc_dominance_pct,
            btc_trend_1d=trend_24h.direction,
            btc_trend_7d=trend_7d.direction,
        )

        warnings: list[str] = []
        if trend_24h.direction == "indeterminate":
            warnings.append(
                "indeterminate_trend_24h: no hay snapshot en history cercano a 24h atrás"
            )
        if trend_7d.direction == "indeterminate":
            warnings.append("indeterminate_trend_7d: history no cubre 7d aún (auto-popula con uso)")

        ctx.deps.log.info(
            "tool.get_market_dominance",
            btc_dom=snap.btc_dominance_pct,
            eth_dom=snap.eth_dominance_pct,
            regime=regime,
            trend_24h=trend_24h.direction,
            trend_7d=trend_7d.direction,
        )

        return ToolResult(
            data=MarketDominanceOut(
                btc_dominance_pct=snap.btc_dominance_pct,
                eth_dominance_pct=snap.eth_dominance_pct,
                total3_share_pct=snap.total3_share_pct,
                total_market_cap_usd=snap.total_market_cap_usd,
                btc_dominance_trend_24h=trend_24h,
                btc_dominance_trend_7d=trend_7d,
                regime=regime,
                interpretation=_interpret(
                    btc_dom=snap.btc_dominance_pct,
                    eth_dom=snap.eth_dominance_pct,
                    total3=snap.total3_share_pct,
                    regime=regime,
                    trend_24h=trend_24h,
                    trend_7d=trend_7d,
                ),
            ),
            provenance=Provenance(
                source="external.coingecko:global",
                as_of=snap.fetched_at,
                rows=1,
                warnings=warnings,
            ),
        )
