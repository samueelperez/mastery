"""Tools for perpetual-specific market data: funding rate, open interest.

These are cripto-specific signals that retail dashboards usually omit. They
materialize the difference between "looking at price" and "looking at the
positioning behind the price". A funding rate persistently positive means
longs pay shorts each 8h — overcrowded directional skew. A rising OI with
flat price is a squeeze building.

Both tools open a `BinanceAdapter` on demand and close it before returning,
so they don't share lifecycle with `LiveIngestion`. Latency ~200ms, fine for
agent calls (1-2 per analysis at most).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

from app.agent.deps import AgentDeps
from app.agent.tools._envelope import Provenance, ToolResult
from app.data.binance_adapter import EXCHANGE_NAME, BinanceAdapter
from app.data.exchange_context import ExchangeContext


class FundingRateOut(BaseModel):
    """Funding rate snapshot for a perpetual contract."""

    symbol: str
    current_rate_pct: float  # current funding rate in % (e.g. 0.01 = 0.01% per 8h)
    next_funding_ts: datetime  # when the next funding is paid
    avg_7d_pct: float  # average over last 21 fundings (~7d), in %
    cumulative_7d_pct: float  # sum of last 21 fundings → annualized cost approx
    bias: str  # "long_pays" / "short_pays" / "neutral"
    interpretation: str  # one-line human reading


class OpenInterestOut(BaseModel):
    """Open interest snapshot."""

    symbol: str
    current_oi_base: float  # OI in base currency (e.g. BTC)
    current_oi_usdt: float  # OI in USDT
    delta_24h_pct: float  # % change vs 24h ago
    trend_7d: str  # "rising" / "falling" / "stable"
    interpretation: str


def _interpret_funding(rate: float, cum_7d: float) -> tuple[str, str]:
    """Devuelve (bias_label, interpretation_es). Convención:
    rate y cum_7d en % (no decimal)."""
    if rate > 0.05:  # > 0.05% por 8h ≈ 55%/año anualizado
        bias = "long_pays"
        return bias, (
            f"Funding muy positivo ({rate:.3f}%/8h): los longs pagan a los shorts. "
            f"Mercado sobreapalancado al alza — vulnerable a squeeze bajista."
        )
    if rate > 0.01:
        bias = "long_pays"
        return bias, (
            f"Funding positivo ({rate:.3f}%/8h): bias alcista en perpetuos. "
            f"Coste de mantener long ≈ {cum_7d:.2f}% en 7d."
        )
    if rate < -0.05:
        bias = "short_pays"
        return bias, (
            f"Funding muy negativo ({rate:.3f}%/8h): los shorts pagan a los longs. "
            f"Mercado sobreapalancado a la baja — vulnerable a squeeze alcista."
        )
    if rate < -0.01:
        bias = "short_pays"
        return bias, (
            f"Funding negativo ({rate:.3f}%/8h): bias bajista en perpetuos. "
            f"Coste de mantener short ≈ {-cum_7d:.2f}% en 7d."
        )
    return "neutral", f"Funding neutro ({rate:.3f}%/8h): sin sesgo direccional fuerte en perpetuos."


def _interpret_oi(delta_pct: float, trend: str) -> str:
    if trend == "rising" and delta_pct > 5:
        return (
            f"OI subiendo fuerte (+{delta_pct:.1f}% 24h): dinero nuevo entra al "
            f"mercado. Confirma el movimiento; cuidado con sobreposicionamiento."
        )
    if trend == "falling" and delta_pct < -5:
        return (
            f"OI bajando fuerte ({delta_pct:.1f}% 24h): cierre masivo de "
            f"posiciones. Posible cap del movimiento o capitulación."
        )
    if trend == "rising":
        return f"OI en tendencia alcista ({delta_pct:+.1f}% 24h)."
    if trend == "falling":
        return f"OI en tendencia bajista ({delta_pct:+.1f}% 24h)."
    return f"OI estable ({delta_pct:+.1f}% 24h)."


def register_perps_data_tools(agent: Agent[AgentDeps, object]) -> None:
    @agent.tool
    async def get_funding_rate(
        ctx: RunContext[AgentDeps],
        symbol: str,
    ) -> ToolResult[FundingRateOut]:
        """Current funding rate + 7d cumulative para un perpetuo USDT-M.

        Funding positivo persistente = longs pagan a los shorts (mercado
        sobreapalancado al alza, vulnerable a squeeze bajista). Negativo = lo
        contrario. Magnitudes: >0.05%/8h es extremo (>~55% APR de coste).

        Cita estos números cuando el setup involucra holding multi-día — el
        funding cumulativo es coste real ignorado por traders nuevos.
        """
        symbol = symbol.upper()
        adapter = BinanceAdapter(ExchangeContext.MAINNET_RO)
        try:
            current = await adapter.fetch_funding_rate(symbol)
            history = await adapter.fetch_funding_rate_history(symbol, limit=21)
        finally:
            await adapter.close()

        # CCXT normaliza fundingRate como decimal (0.0001 = 0.01%). Pasamos a %.
        current_rate_pct = float(current.get("fundingRate", 0.0)) * 100.0
        next_ts_ms = current.get("nextFundingTimestamp") or current.get("fundingTimestamp")
        next_ts = (
            datetime.fromtimestamp(int(next_ts_ms) / 1000, tz=UTC)
            if next_ts_ms
            else datetime.now(tz=UTC)
        )

        rates_pct = [float(h.get("fundingRate", 0.0)) * 100.0 for h in history]
        avg_7d = sum(rates_pct) / len(rates_pct) if rates_pct else current_rate_pct
        cum_7d = sum(rates_pct)
        bias, interp = _interpret_funding(current_rate_pct, cum_7d)

        ctx.deps.log.info(
            "tool.get_funding_rate",
            symbol=symbol,
            rate_pct=round(current_rate_pct, 4),
            cum_7d_pct=round(cum_7d, 3),
        )
        return ToolResult(
            data=FundingRateOut(
                symbol=symbol,
                current_rate_pct=round(current_rate_pct, 4),
                next_funding_ts=next_ts,
                avg_7d_pct=round(avg_7d, 4),
                cumulative_7d_pct=round(cum_7d, 3),
                bias=bias,
                interpretation=interp,
            ),
            provenance=Provenance(
                source=f"binance_usdm:funding_rate:{symbol}",
                as_of=datetime.now(tz=UTC),
                rows=len(rates_pct) + 1,
                warnings=(
                    [] if rates_pct else ["no funding history available"]
                ),
            ),
        )

    @agent.tool
    async def get_open_interest(
        ctx: RunContext[AgentDeps],
        symbol: str,
    ) -> ToolResult[OpenInterestOut]:
        """Current open interest + 24h delta + 7d trend para un perpetuo USDT-M.

        OI subiendo con precio subiendo = dinero nuevo entrando (confirma
        trend). OI subiendo con precio plano = squeeze building. OI bajando
        con precio subiendo = short cover, sin dinero nuevo. Cita esto para
        diferenciar trends sostenibles de squeezes.
        """
        symbol = symbol.upper()
        adapter = BinanceAdapter(ExchangeContext.MAINNET_RO)
        oi_value_warning: str | None = None
        try:
            current = await adapter.fetch_open_interest(symbol)
            # 1h x 168 = 7 días para trend
            history = await adapter.fetch_open_interest_history(
                symbol, timeframe="1h", limit=168
            )
            current_amt = float(
                current.get("openInterestAmount")
                or current.get("openInterest")
                or 0.0
            )
            current_value = float(current.get("openInterestValue") or 0.0)
            # Binance USDM `/fapi/v1/openInterest` no devuelve `openInterestValue`
            # directamente — CCXT lo deja en None y nuestro `or 0.0` lo
            # convierte en cero. Derivamos via OI_base × last_price para que
            # la card del frontend muestre un valor real.
            if current_value == 0.0 and current_amt > 0.0:
                try:
                    ticker = await adapter.fetch_ticker(symbol)
                    last_price = float(
                        ticker.get("last")
                        or ticker.get("close")
                        or ticker.get("mark")
                        or 0.0
                    )
                    if last_price > 0:
                        current_value = current_amt * last_price
                    else:
                        oi_value_warning = "oi_usdt_unavailable: ticker sin last/close/mark"
                except Exception as exc:
                    ctx.deps.log.warning(
                        "tool.get_open_interest.value_derive_failed",
                        symbol=symbol,
                        error=str(exc),
                    )
                    oi_value_warning = f"oi_usdt_unavailable: {exc}"
        finally:
            await adapter.close()

        # 24h ago = 24 entries back en 1h history
        if len(history) >= 25:
            oi_24h_ago = float(
                history[-25].get("openInterestAmount")
                or history[-25].get("openInterest")
                or 0.0
            )
            delta_24h_pct = (
                ((current_amt - oi_24h_ago) / oi_24h_ago * 100.0) if oi_24h_ago > 0 else 0.0
            )
        else:
            delta_24h_pct = 0.0

        # 7d trend: comparar primer cuarto vs último cuarto
        if len(history) >= 16:
            first_q = sum(
                float(h.get("openInterestAmount") or h.get("openInterest") or 0.0)
                for h in history[: len(history) // 4]
            ) / max(len(history) // 4, 1)
            last_q = sum(
                float(h.get("openInterestAmount") or h.get("openInterest") or 0.0)
                for h in history[-len(history) // 4 :]
            ) / max(len(history) // 4, 1)
            change = (last_q - first_q) / first_q * 100.0 if first_q > 0 else 0.0
            trend = "rising" if change > 5 else "falling" if change < -5 else "stable"
        else:
            trend = "stable"

        interp = _interpret_oi(delta_24h_pct, trend)

        ctx.deps.log.info(
            "tool.get_open_interest",
            symbol=symbol,
            oi_value=round(current_value, 2),
            delta_24h_pct=round(delta_24h_pct, 2),
            trend=trend,
        )
        return ToolResult(
            data=OpenInterestOut(
                symbol=symbol,
                current_oi_base=round(current_amt, 4),
                current_oi_usdt=round(current_value, 2),
                delta_24h_pct=round(delta_24h_pct, 2),
                trend_7d=trend,
                interpretation=interp,
            ),
            provenance=Provenance(
                source=f"binance_usdm:open_interest:{symbol}",
                as_of=datetime.now(tz=UTC),
                rows=len(history) + 1,
                warnings=[
                    *(
                        ["limited OI history (<25h)"]
                        if len(history) < 25
                        else []
                    ),
                    *([oi_value_warning] if oi_value_warning else []),
                ],
            ),
        )
