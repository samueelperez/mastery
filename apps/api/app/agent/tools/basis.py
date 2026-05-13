"""get_basis tool — spot vs perp price gap (basis) with 30d percentile bands.

The basis (perp_price − spot_price) anchors via funding rate: when basis runs
hot, funding turns extreme until arbitrageurs collapse it back. Sustained
premium = leverage caliente cargando largos; sustained discount = leverage
cargando shorts. Both precede mean-reversion.

Limited to USDT pairs where both spot and perp exist on Binance. For pairs
without a spot listing (some altperps), the tool returns `regime='unavailable'`
with a warning instead of failing the chat.

Data flow:
- Current basis: spot ticker + perp ticker (cached 60s in Redis).
- 30-day history: spot 1h OHLCV + perp 1h OHLCV → basis_pct per bar →
  P10 / P90 percentiles (cached 1h in Redis).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

from app.agent.deps import AgentDeps
from app.agent.tools._envelope import Provenance, ToolResult
from app.core.broadcasting.pubsub import get_client
from app.core.exchanges.binance_adapter import BinanceAdapter
from app.core.exchanges.exchange_context import ExchangeContext
from app.core.exchanges.spot_adapter import BinanceSpotAdapter

# Cache TTLs. Current basis updates fast (60s ≈ a few perp ticks);
# percentile bands move slowly (1h is plenty — 30d window barely shifts hourly).
_CURRENT_CACHE_TTL_SECONDS = 60
_HISTORY_CACHE_TTL_SECONDS = 3600

_HISTORY_BARS = 720  # 30 days of 1h bars

# Regime thresholds (in % basis). The percentile-aware classifier promotes
# to extreme only when both the absolute level AND the historical percentile
# agree — avoids labelling a 0.05% basis as "extreme" just because the last
# 30d were all near zero.
_PREMIUM_MIN_PCT = 0.10
_EXTREME_PREMIUM_MIN_PCT = 0.25
_DISCOUNT_MAX_PCT = -0.10
_EXTREME_DISCOUNT_MAX_PCT = -0.25


BasisRegime = Literal[
    "extreme_premium",
    "premium",
    "neutral",
    "discount",
    "extreme_discount",
    "unavailable",
]


class BasisOut(BaseModel):
    symbol: str
    spot_price: float
    perp_price: float
    basis_abs: float  # perp − spot, in price units
    basis_pct: float  # 100 * (perp − spot) / spot
    basis_p90_30d_pct: float
    basis_p10_30d_pct: float
    basis_median_30d_pct: float
    regime: BasisRegime
    interpretation: str


# -----------------------------------------------------------------------------
# Pure helpers
# -----------------------------------------------------------------------------


def compute_basis_pct(spot: float, perp: float) -> float:
    """Return ``100 * (perp - spot) / spot`` with a zero-spot guard."""
    if spot <= 0:
        return 0.0
    return (perp - spot) / spot * 100.0


def percentile(values: list[float], q: float) -> float:
    """Return the q-th percentile (q in [0, 1]) with linear interpolation.

    Matches numpy's default behavior; we don't import numpy here to keep
    this helper test-only-dependency-free.
    """
    if not values:
        return 0.0
    sorted_v = sorted(values)
    n = len(sorted_v)
    if n == 1:
        return sorted_v[0]
    rank = q * (n - 1)
    lo_idx = int(rank)
    hi_idx = min(lo_idx + 1, n - 1)
    frac = rank - lo_idx
    return sorted_v[lo_idx] * (1 - frac) + sorted_v[hi_idx] * frac


def classify_basis_regime(
    *,
    basis_pct: float,
    p90: float,
    p10: float,
) -> BasisRegime:
    """Combine absolute level + 30d percentile band.

    "extreme_premium" requires BOTH the level to exceed the high absolute
    threshold AND the current observation to sit at-or-above the 30d P90.
    A flat-basis market that happens to print 0.30% won't be labelled
    extreme just because its P90 was 0.05 — we want extremes that ARE
    historically extreme for this symbol.
    """
    if basis_pct >= _EXTREME_PREMIUM_MIN_PCT and basis_pct >= p90:
        return "extreme_premium"
    if basis_pct >= _PREMIUM_MIN_PCT:
        return "premium"
    if basis_pct <= _EXTREME_DISCOUNT_MAX_PCT and basis_pct <= p10:
        return "extreme_discount"
    if basis_pct <= _DISCOUNT_MAX_PCT:
        return "discount"
    return "neutral"


def build_interpretation(
    *,
    regime: BasisRegime,
    basis_pct: float,
    p90: float,
    p10: float,
) -> str:
    if regime == "extreme_premium":
        return (
            f"Basis extremo +{basis_pct:.3f}% (P90 30d = +{p90:.3f}%). "
            f"Leverage long muy caliente — el funding va a forzar la convergencia. "
            f"Asimetría al downside: cualquier catalyst bajista desinfla rápido. "
            f"NO añadir longs apalancados aquí; considera tomar parciales."
        )
    if regime == "extreme_discount":
        return (
            f"Basis extremo {basis_pct:.3f}% (P10 30d = {p10:.3f}%). "
            f"Leverage short muy caliente — funding negativo está pagando a longs, "
            f"setup clásico de short squeeze. Asimetría al upside; "
            f"cuidado con SL ajustado en short."
        )
    if regime == "premium":
        return (
            f"Basis en premium ({basis_pct:+.3f}%): perp por encima del spot. "
            f"Sentimiento alcista predominante en derivados; aún no extremo "
            f"(P90 = +{p90:.3f}%). Trend longs viables, sizing normal."
        )
    if regime == "discount":
        return (
            f"Basis en discount ({basis_pct:+.3f}%): perp por debajo del spot. "
            f"Sentimiento bajista en derivados; no extremo aún (P10 = {p10:.3f}%). "
            f"Trend shorts viables, sizing normal."
        )
    if regime == "unavailable":
        return (
            "Spot listing no disponible para este símbolo en Binance — basis "
            "no computable. Para basis necesitas mercado spot + perp activos "
            "del mismo par."
        )
    return (
        f"Basis neutro ({basis_pct:+.3f}%): spot y perp prácticamente alineados. "
        f"Sin sesgo de derivados que añadir a la tesis."
    )


# -----------------------------------------------------------------------------
# I/O with Redis caching
# -----------------------------------------------------------------------------


def _cache_key_current(symbol: str) -> str:
    return f"basis:current:{symbol.upper()}"


def _cache_key_history(symbol: str) -> str:
    return f"basis:history:{symbol.upper()}"


async def _load_cached_current(symbol: str) -> dict[str, Any] | None:
    client = get_client()
    raw = await client.get(_cache_key_current(symbol))
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


async def _store_cached_current(symbol: str, payload: dict[str, Any]) -> None:
    client = get_client()
    await client.set(
        _cache_key_current(symbol),
        json.dumps(payload),
        ex=_CURRENT_CACHE_TTL_SECONDS,
    )


async def _load_cached_history(symbol: str) -> list[float] | None:
    client = get_client()
    raw = await client.get(_cache_key_history(symbol))
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [float(x) for x in data if isinstance(x, (int, float))]
    except Exception:
        return None
    return None


async def _store_cached_history(symbol: str, basis_history: list[float]) -> None:
    client = get_client()
    await client.set(
        _cache_key_history(symbol),
        json.dumps(basis_history),
        ex=_HISTORY_CACHE_TTL_SECONDS,
    )


# -----------------------------------------------------------------------------
# Composition
# -----------------------------------------------------------------------------


async def _fetch_current_prices(symbol: str) -> tuple[float, float] | None:
    """Return (spot_price, perp_price). None on ANY fetch failure (spot
    unavailable, perp API hiccup, network glitch). Caller surfaces the
    'unavailable' regime gracefully — we don't want a transient Binance
    error to crash a chat turn."""
    spot_adapter = BinanceSpotAdapter()
    perp_adapter = BinanceAdapter(ExchangeContext.MAINNET_RO)
    try:
        try:
            spot_ticker = await spot_adapter.fetch_ticker(symbol)
        except Exception:
            return None
        try:
            perp_ticker = await perp_adapter.fetch_ticker(symbol)
        except Exception:
            return None
    finally:
        await spot_adapter.close()
        await perp_adapter.close()

    def _resolve(t: dict[str, Any]) -> float:
        return float(t.get("last") or t.get("close") or t.get("mark") or 0.0)

    spot = _resolve(spot_ticker)
    perp = _resolve(perp_ticker)
    if spot <= 0 or perp <= 0:
        return None
    return spot, perp


async def _fetch_basis_history(symbol: str) -> list[float]:
    """30-day basis history as `basis_pct` per closed 1h bar. Empty list on
    fetch failure (caller falls back to using only the current observation
    for the regime classifier)."""
    spot_adapter = BinanceSpotAdapter()
    perp_adapter = BinanceAdapter(ExchangeContext.MAINNET_RO)
    try:
        try:
            spot_bars = await spot_adapter.fetch_ohlcv_page(symbol, "1h", limit=_HISTORY_BARS)
        except Exception:
            return []
        perp_bars = await perp_adapter.fetch_ohlcv_page(symbol, "1h", limit=_HISTORY_BARS)
    finally:
        await spot_adapter.close()
        await perp_adapter.close()

    # Align by timestamp — bars from different exchanges can differ by a
    # bar at the edges; join on ts.
    spot_by_ts = {bar.ts: bar.c for bar in spot_bars}
    history: list[float] = []
    for bar in perp_bars:
        spot_c = spot_by_ts.get(bar.ts)
        if spot_c is None or spot_c <= 0:
            continue
        history.append(compute_basis_pct(spot=spot_c, perp=bar.c))
    return history


# -----------------------------------------------------------------------------
# Tool registration
# -----------------------------------------------------------------------------


def register_basis_tool(agent: Agent[AgentDeps, object]) -> None:
    @agent.tool
    async def get_basis(
        ctx: RunContext[AgentDeps],
        symbol: str,
    ) -> ToolResult[BasisOut]:
        """Spot vs perp basis con bandas de percentil 30d y regime.

        Devuelve `basis_pct` actual, percentiles P10/P50/P90 sobre 30d de
        1h OHLCV alineadas, y `regime` ∈ {extreme_premium, premium,
        neutral, discount, extreme_discount, unavailable}.

        - `extreme_premium`: leverage long muy caliente, asimetría al downside.
          Cuando lo veas, NO añadas longs apalancados; considera tomar
          parciales si ya estás dentro.
        - `extreme_discount`: leverage short muy caliente, asimetría al
          upside (short squeeze setup). Combina con `get_perps_dynamics`
          para confirmar.
        - `premium`/`discount`: sentimiento direccional pero no extremo
          — normal sizing.
        - `neutral`: sin sesgo de derivados.
        - `unavailable`: este símbolo no tiene listing spot en Binance
          (algún altperp). El tool no falla — solo no aporta señal aquí.

        Latencia: cache HIT ~50ms, MISS hasta 1-2s (4 REST calls). Cachéa
        current 60s e history 30d 1h. Cita esta tool cuando opere símbolos
        con spot listing (BTC/ETH/SOL/major alts) y el régimen sea no-neutral.
        """
        symbol = symbol.upper()
        cutoff = datetime.now(tz=UTC)
        warnings: list[str] = []

        # Current prices: cache → fetch
        cached_current = await _load_cached_current(symbol)
        if cached_current is not None:
            current = cached_current
        else:
            prices = await _fetch_current_prices(symbol)
            if prices is None:
                # Spot listing unavailable — emit a graceful response.
                ctx.deps.log.info("tool.get_basis.spot_unavailable", symbol=symbol)
                return ToolResult(
                    data=BasisOut(
                        symbol=symbol,
                        spot_price=0.0,
                        perp_price=0.0,
                        basis_abs=0.0,
                        basis_pct=0.0,
                        basis_p90_30d_pct=0.0,
                        basis_p10_30d_pct=0.0,
                        basis_median_30d_pct=0.0,
                        regime="unavailable",
                        interpretation=build_interpretation(
                            regime="unavailable",
                            basis_pct=0.0,
                            p90=0.0,
                            p10=0.0,
                        ),
                    ),
                    provenance=Provenance(
                        source=f"binance_spot+binance_usdm:basis:{symbol}",
                        as_of=cutoff,
                        rows=0,
                        warnings=["spot_listing_unavailable"],
                    ),
                )
            spot, perp = prices
            current = {"spot_price": spot, "perp_price": perp}
            await _store_cached_current(symbol, current)

        spot_price = float(current["spot_price"])
        perp_price = float(current["perp_price"])
        basis_pct = compute_basis_pct(spot=spot_price, perp=perp_price)

        # History: cache → fetch
        history = await _load_cached_history(symbol)
        if history is None:
            history = await _fetch_basis_history(symbol)
            if history:
                await _store_cached_history(symbol, history)

        if not history:
            warnings.append("history_fetch_failed: percentiles based on current bar only")
            p10 = p90 = median = basis_pct
        else:
            p10 = percentile(history, 0.10)
            p90 = percentile(history, 0.90)
            median = percentile(history, 0.50)
            if len(history) < 100:
                warnings.append(
                    f"history_short: {len(history)} hourly bars (<100); "
                    "percentile bands less stable"
                )

        regime = classify_basis_regime(basis_pct=basis_pct, p90=p90, p10=p10)
        interpretation = build_interpretation(
            regime=regime,
            basis_pct=basis_pct,
            p90=p90,
            p10=p10,
        )

        ctx.deps.log.info(
            "tool.get_basis",
            symbol=symbol,
            basis_pct=round(basis_pct, 4),
            regime=regime,
            history_n=len(history),
        )

        return ToolResult(
            data=BasisOut(
                symbol=symbol,
                spot_price=round(spot_price, 4),
                perp_price=round(perp_price, 4),
                basis_abs=round(perp_price - spot_price, 4),
                basis_pct=round(basis_pct, 4),
                basis_p90_30d_pct=round(p90, 4),
                basis_p10_30d_pct=round(p10, 4),
                basis_median_30d_pct=round(median, 4),
                regime=regime,
                interpretation=interpretation,
            ),
            provenance=Provenance(
                source=f"binance_spot+binance_usdm:basis:{symbol}",
                as_of=cutoff,
                rows=len(history) + 1,
                warnings=warnings,
            ),
        )
