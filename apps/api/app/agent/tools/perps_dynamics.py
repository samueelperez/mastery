"""get_perps_dynamics tool — OI deltas multi-window + funding velocity + squeeze setup.

Where `get_funding_rate` and `get_open_interest` provide current snapshots,
this tool provides the **derivatives**: how fast OI is accumulating across
1h/4h/24h windows, the funding rate's velocity (change between two
consecutive 8h payments), and whether the current funding is in the
extreme tail of the last 90 days. From those primitives it derives a
squeeze-setup heuristic that flags imminent leverage unwinds.

Why this matters: the level of OI tells you crowdedness; the **slope** of
OI tells you whether leverage is loading right now (the dangerous part).
The level of funding tells you who pays whom; its **velocity** tells you
whether sentiment is accelerating into capitulation or euphoria. Together
with price action they expose squeeze setups that no per-snapshot tool
can catch.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

from app.agent.deps import AgentDeps
from app.agent.tools._envelope import Provenance, ToolResult
from app.core.exchanges.binance_adapter import BinanceAdapter
from app.core.exchanges.exchange_context import ExchangeContext
from app.storage.ohlcv_repo import fetch_range

# Funding extreme threshold: top quantile of |funding| over the lookback
# window. 90% picks the genuinely tail-heavy fundings without overfitting
# to single outliers.
_FUNDING_EXTREME_QUANTILE = 0.90

# Squeeze heuristic threshold: a "loading" OI rise within 24h. 5% is the
# same threshold the existing `get_open_interest` tool uses for its trend
# label, kept in sync to avoid contradictory readings.
_OI_LOADING_THRESHOLD_PCT = 5.0

# Squeeze heuristic threshold for price flatness vs the leverage skew. We
# look for "price has barely moved while OI loaded one side" — a textbook
# squeeze setup. 1.5% on 24h is roughly 0.5σ for BTC on a typical week.
_PRICE_FLAT_THRESHOLD_PCT = 1.5


OIPriceDivergenceLabel = Literal[
    "both_up",
    "oi_up_price_down",
    "oi_down_price_up",
    "both_down",
    "neutral",
]

SqueezeSetup = Literal["long_squeeze", "short_squeeze", "none"]


class PerpsDynamicsOut(BaseModel):
    """Derivative-level perpetuals signals: how the market is *changing*."""

    symbol: str

    # OI deltas (% change vs N hours ago)
    oi_delta_1h_pct: float
    oi_delta_4h_pct: float
    oi_delta_24h_pct: float

    # Price delta (% change vs 24h ago) — needed to interpret OI delta direction.
    price_delta_24h_pct: float

    # Combined OI vs Price interpretation.
    oi_price_divergence: OIPriceDivergenceLabel

    # Funding dynamics.
    funding_current_pct: float
    funding_prev_pct: float
    funding_velocity_8h_pct: float  # current - prev (in % per 8h)
    funding_extreme: bool  # |current| above P90 of 90d history
    funding_p90_abs_pct: float  # the threshold itself, for transparency
    funding_history_n: int

    # Derived setup.
    squeeze_setup: SqueezeSetup
    interpretation: str


# -----------------------------------------------------------------------------
# Pure helpers — testable without DB / network
# -----------------------------------------------------------------------------


def _oi_value(entry: dict[str, Any]) -> float | None:
    """Resolve OI amount from a CCXT history entry (different brokers expose
    the same field under different keys)."""
    for key in ("openInterestAmount", "openInterest", "openInterestValue"):
        v = entry.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return None


def compute_oi_delta_pct(
    current_oi: float,
    history: list[dict[str, Any]],
    *,
    entries_back: int,
) -> float:
    """Compute % change of OI vs the entry `entries_back` steps ago.

    `history` is assumed chronologically ordered (oldest first), which
    matches what CCXT's `fetch_open_interest_history` returns. If history
    is too short OR the historic value is non-positive, return 0.0
    (caller can decide whether to surface that as a warning).
    """
    if entries_back <= 0 or current_oi <= 0:
        return 0.0
    if len(history) < entries_back + 1:
        return 0.0
    past = _oi_value(history[-(entries_back + 1)])
    if past is None or past <= 0:
        return 0.0
    return (current_oi - past) / past * 100.0


def compute_p90_abs(values: list[float]) -> float:
    """Return the 90th percentile of |values|. Used to define "extreme"
    funding without dragging in scipy/numpy. Linear interpolation between
    bracketing samples — same convention scipy uses by default.
    """
    if not values:
        return 0.0
    sorted_abs = sorted(abs(v) for v in values)
    n = len(sorted_abs)
    if n == 1:
        return sorted_abs[0]
    rank = _FUNDING_EXTREME_QUANTILE * (n - 1)
    lo_idx = int(rank)
    hi_idx = min(lo_idx + 1, n - 1)
    frac = rank - lo_idx
    return sorted_abs[lo_idx] * (1 - frac) + sorted_abs[hi_idx] * frac


def classify_oi_price_divergence(
    oi_delta_pct: float,
    price_delta_pct: float,
    *,
    oi_threshold: float = 1.0,
    price_threshold: float = 0.5,
) -> OIPriceDivergenceLabel:
    """Bucket the (ΔOI, Δprice) pair into a directional label.

    Thresholds avoid labelling tiny movements as a trend:
    - `oi_threshold=1.0` (% in window) — sub-1% OI change is noise.
    - `price_threshold=0.5` (% in window) — sub-0.5% price move is noise.
    """
    oi_up = oi_delta_pct >= oi_threshold
    oi_down = oi_delta_pct <= -oi_threshold
    price_up = price_delta_pct >= price_threshold
    price_down = price_delta_pct <= -price_threshold
    if oi_up and price_up:
        return "both_up"
    if oi_up and price_down:
        return "oi_up_price_down"
    if oi_down and price_up:
        return "oi_down_price_up"
    if oi_down and price_down:
        return "both_down"
    return "neutral"


def classify_squeeze_setup(
    *,
    funding_current_pct: float,
    funding_extreme: bool,
    oi_delta_24h_pct: float,
    price_delta_24h_pct: float,
) -> SqueezeSetup:
    """Squeeze setup heuristic. Returns 'long_squeeze' (longs vulnerable),
    'short_squeeze' (shorts vulnerable), or 'none'.

    Long squeeze = longs apilados, vulnerables a un drop:
        funding strongly POSITIVE (longs paying) + extreme + OI loading +
        price has barely confirmed the lean (flat or only slightly up).
        Crowding without the price to back it up.

    Short squeeze = shorts apilados, vulnerables a un rally:
        funding strongly NEGATIVE (shorts paying) + extreme + OI loading +
        price has barely confirmed the lean (flat or only slightly down).
    """
    if not funding_extreme:
        return "none"
    if oi_delta_24h_pct < _OI_LOADING_THRESHOLD_PCT:
        # OI not loading materially → no fresh leverage to unwind.
        return "none"
    price_flat = abs(price_delta_24h_pct) < _PRICE_FLAT_THRESHOLD_PCT
    if funding_current_pct > 0 and (price_flat or price_delta_24h_pct > 0):
        return "long_squeeze"
    if funding_current_pct < 0 and (price_flat or price_delta_24h_pct < 0):
        return "short_squeeze"
    return "none"


def build_interpretation(
    *,
    squeeze_setup: SqueezeSetup,
    oi_price_divergence: OIPriceDivergenceLabel,
    funding_current_pct: float,
    funding_p90_abs_pct: float,
    funding_velocity_8h_pct: float,
    oi_delta_24h_pct: float,
    price_delta_24h_pct: float,
) -> str:
    """Single-line human reading. Surfaces the most-load-bearing signal.

    Public so tests can pin the message shape without spinning up the tool.
    """
    if squeeze_setup == "long_squeeze":
        return (
            f"Long-squeeze setup: funding {funding_current_pct:+.3f}%/8h "
            f"(extremo, P90={funding_p90_abs_pct:.3f}), OI +"
            f"{oi_delta_24h_pct:.1f}% en 24h, precio "
            f"{price_delta_24h_pct:+.2f}%. Longs apilados sin que el "
            f"precio confirme — vulnerable a drop si entra catalizador bajista."
        )
    if squeeze_setup == "short_squeeze":
        return (
            f"Short-squeeze setup: funding {funding_current_pct:+.3f}%/8h "
            f"(extremo, P90={funding_p90_abs_pct:.3f}), OI +"
            f"{oi_delta_24h_pct:.1f}% en 24h, precio "
            f"{price_delta_24h_pct:+.2f}%. Shorts apilados sin que el "
            f"precio confirme — vulnerable a rally si entra catalizador alcista."
        )
    div_msg = {
        "both_up": "OI y precio subiendo juntos — dinero nuevo confirma trend",
        "oi_up_price_down": "OI sube con precio bajando — shorts cargando",
        "oi_down_price_up": "OI baja con precio subiendo — short cover, sin demanda nueva",
        "both_down": "OI y precio bajando — longs liquidando, capitulación parcial",
        "neutral": "OI y precio sin cambios materiales — mercado estable",
    }[oi_price_divergence]
    ratio = abs(funding_current_pct) / funding_p90_abs_pct if funding_p90_abs_pct > 0 else 0.0
    return (
        f"{div_msg}. Funding velocity {funding_velocity_8h_pct:+.3f}pp/8h, "
        f"|current|/P90 = {ratio:.2f}."
    )


# -----------------------------------------------------------------------------
# Tool registration
# -----------------------------------------------------------------------------


def register_perps_dynamics_tool(agent: Agent[AgentDeps, object]) -> None:
    @agent.tool
    async def get_perps_dynamics(
        ctx: RunContext[AgentDeps],
        symbol: str,
    ) -> ToolResult[PerpsDynamicsOut]:
        """Derivada del estado perpetuo — OI deltas 1h/4h/24h, funding
        velocity, detección de extremos sobre P90 de 90d, y heurística de
        squeeze setup.

        Complementa a `get_funding_rate` y `get_open_interest`: ESAS dan
        snapshot, ESTA da la dinámica. En perps, las mejores asimetrías
        nacen del crowding + leverage cargando antes de que el precio
        confirme — momentos que solo se ven mirando las derivadas.

        Squeeze setups:
        - `long_squeeze`: funding ++ extremo + OI cargando + precio flat o
          ligeramente alcista. Longs apilados sin price action a su favor —
          vulnerable a drop si llega catalizador bajista. Mensaje: NO te
          apalanques largo aquí; considera reducir si ya tienes long.
        - `short_squeeze`: funding -- extremo + OI cargando + precio flat
          o ligeramente bajista. Inverso: shorts apilados, asimetría al
          alza si llega catalizador.
        - `none`: ni un cuadro extremo ni leverage cargándose → no hay
          edge de "squeeze setup" hoy.

        Cítalo en `confluences` cuando sea no-none. Para BTCUSDT el dato
        es global; para alts es por contrato. Latencia ~300-500ms (varias
        llamadas REST encadenadas).
        """
        symbol = symbol.upper()
        cutoff = datetime.now(tz=UTC)

        adapter = BinanceAdapter(ExchangeContext.MAINNET_RO)
        try:
            current_oi = await adapter.fetch_open_interest(symbol)
            # 168 entries × 1h = 7d. Suficiente para deltas 1h/4h/24h.
            oi_history = await adapter.fetch_open_interest_history(
                symbol, timeframe="1h", limit=168
            )
            current_funding = await adapter.fetch_funding_rate(symbol)
            # 270 entries × 8h ≈ 90d. P90 sobre 90d evita estacionalidad.
            funding_history = await adapter.fetch_funding_rate_history(symbol, limit=270)
        finally:
            await adapter.close()

        current_oi_amt = _oi_value(current_oi) or 0.0

        # OI deltas in 3 windows. 1h = 1 entry back, 4h = 4 back, 24h = 24 back.
        oi_delta_1h = compute_oi_delta_pct(current_oi_amt, oi_history, entries_back=1)
        oi_delta_4h = compute_oi_delta_pct(current_oi_amt, oi_history, entries_back=4)
        oi_delta_24h = compute_oi_delta_pct(current_oi_amt, oi_history, entries_back=24)

        # Price 24h delta from OHLCV 1h (25 bars: now + 24h ago).
        price_delta_24h_pct = 0.0
        price_warning: str | None = None
        try:
            async with ctx.deps.session_factory() as session:
                bars = await fetch_range(
                    session,
                    exchange=ctx.deps.exchange,
                    symbol=symbol,
                    timeframe="1h",
                    until=cutoff,
                    limit=25,
                )
            if len(bars) >= 25:
                last_close = float(bars[-1].c)
                past_close = float(bars[-25].c)
                if past_close > 0:
                    price_delta_24h_pct = (last_close - past_close) / past_close * 100.0
            else:
                price_warning = "ohlcv_short: less than 25 1h bars in DB"
        except Exception as exc:
            ctx.deps.log.warning(
                "tool.get_perps_dynamics.ohlcv_failed",
                symbol=symbol,
                error=str(exc),
            )
            price_warning = f"ohlcv_failed: {exc}"

        # Funding metrics. CCXT returns decimal (0.0001 = 0.01%); we work
        # in percentage points throughout.
        funding_current_pct = float(current_funding.get("fundingRate", 0.0)) * 100.0
        funding_history_rates_pct = [
            float(h.get("fundingRate", 0.0)) * 100.0
            for h in funding_history
            if isinstance(h.get("fundingRate", None), (int, float))
        ]
        prev_funding_pct = (
            funding_history_rates_pct[-1] if funding_history_rates_pct else funding_current_pct
        )
        funding_velocity_pct = funding_current_pct - prev_funding_pct
        funding_p90_abs = compute_p90_abs(funding_history_rates_pct)
        funding_extreme = abs(funding_current_pct) > funding_p90_abs and funding_p90_abs > 0

        oi_price_divergence = classify_oi_price_divergence(
            oi_delta_pct=oi_delta_24h,
            price_delta_pct=price_delta_24h_pct,
        )
        squeeze = classify_squeeze_setup(
            funding_current_pct=funding_current_pct,
            funding_extreme=funding_extreme,
            oi_delta_24h_pct=oi_delta_24h,
            price_delta_24h_pct=price_delta_24h_pct,
        )

        interpretation = build_interpretation(
            squeeze_setup=squeeze,
            oi_price_divergence=oi_price_divergence,
            funding_current_pct=funding_current_pct,
            funding_p90_abs_pct=funding_p90_abs,
            funding_velocity_8h_pct=funding_velocity_pct,
            oi_delta_24h_pct=oi_delta_24h,
            price_delta_24h_pct=price_delta_24h_pct,
        )

        out = PerpsDynamicsOut(
            symbol=symbol,
            oi_delta_1h_pct=round(oi_delta_1h, 3),
            oi_delta_4h_pct=round(oi_delta_4h, 3),
            oi_delta_24h_pct=round(oi_delta_24h, 3),
            price_delta_24h_pct=round(price_delta_24h_pct, 3),
            oi_price_divergence=oi_price_divergence,
            funding_current_pct=round(funding_current_pct, 4),
            funding_prev_pct=round(prev_funding_pct, 4),
            funding_velocity_8h_pct=round(funding_velocity_pct, 4),
            funding_extreme=funding_extreme,
            funding_p90_abs_pct=round(funding_p90_abs, 4),
            funding_history_n=len(funding_history_rates_pct),
            squeeze_setup=squeeze,
            interpretation=interpretation,
        )

        warnings: list[str] = []
        if len(oi_history) < 25:
            warnings.append("oi_history_short: <25 hourly entries (24h delta unstable)")
        if len(funding_history_rates_pct) < 30:
            warnings.append("funding_history_short: <30 fundings (P90 unstable)")
        if price_warning:
            warnings.append(price_warning)

        ctx.deps.log.info(
            "tool.get_perps_dynamics",
            symbol=symbol,
            oi_delta_24h=round(oi_delta_24h, 2),
            price_delta_24h=round(price_delta_24h_pct, 2),
            funding_current=round(funding_current_pct, 4),
            funding_extreme=funding_extreme,
            squeeze=squeeze,
        )

        return ToolResult(
            data=out,
            provenance=Provenance(
                source=f"binance_usdm:perps_dynamics:{symbol}",
                as_of=cutoff,
                rows=len(oi_history) + len(funding_history_rates_pct),
                warnings=warnings,
            ),
        )
