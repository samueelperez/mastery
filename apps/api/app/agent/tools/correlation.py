"""get_btc_correlation tool — Pearson rolling correlation between an altcoin
and BTCUSDT on the same timeframe.

En cripto, el ~80% de la varianza de altcoins viene del movimiento de BTC.
Cuando un trader propone un trade en SOLUSDT, saber si está correlado +0.9
con BTC cambia la tesis: no es un setup independiente, es un trade
apalancado sobre BTC. Esta tool materializa esa información.

Si el símbolo del análisis ES BTCUSDT, devolvemos correlation=1.0 trivialmente
y un interpretation acorde.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

import numpy as np
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from app.agent.deps import AgentDeps
from app.agent.tools._envelope import Provenance, ToolResult
from app.agent.tools._time import floor_to_timeframe
from app.core.exchanges.binance_adapter import EXCHANGE_NAME
from app.storage.ohlcv_repo import fetch_range


class CorrelationOut(BaseModel):
    """Pearson correlation snapshot."""

    symbol: str
    reference: str  # e.g. "BTCUSDT"
    timeframe: str
    lookback_bars: int
    pearson: float  # -1..1
    bias_weight_factor: float  # 0..1: cuanto pesa BTC en la decisión
    interpretation: str


def _interpret_correlation(rho: float, symbol: str) -> tuple[float, str]:
    """Devuelve (bias_weight_factor, interpretation_es).

    bias_weight_factor mide cuánto debería el agente "descontar" un bias
    técnico del símbolo cuando está fuertemente correlado con BTC. Si BTC
    está bull y el alt está bull con rho=0.95, ese bull es ~95% prestado
    de BTC — no es señal independiente.
    """
    abs_rho = abs(rho)
    if abs_rho >= 0.85:
        return 0.95, (
            f"Correlación muy alta ({rho:+.2f}) con BTC. {symbol} se mueve "
            f"prácticamente como BTC apalancado — no operes este símbolo "
            f"como tesis independiente; el bias real es BTC."
        )
    if abs_rho >= 0.7:
        return 0.75, (
            f"Correlación alta ({rho:+.2f}) con BTC. La mayoría del movimiento "
            f"de {symbol} es derivado de BTC; el alpha específico es limitado."
        )
    if abs_rho >= 0.5:
        return 0.5, (
            f"Correlación moderada ({rho:+.2f}) con BTC. Movimiento mixto: "
            f"BTC es un input pero hay catalysts propios."
        )
    if abs_rho >= 0.3:
        return 0.25, (
            f"Correlación débil ({rho:+.2f}) con BTC. {symbol} se mueve por "
            f"factores propios mayoritariamente."
        )
    return 0.05, (
        f"Sin correlación significativa ({rho:+.2f}) con BTC. {symbol} es un "
        f"activo independiente del momento."
    )


def register_correlation_tool(agent: Agent[AgentDeps, object]) -> None:
    @agent.tool
    async def get_btc_correlation(
        ctx: RunContext[AgentDeps],
        symbol: str,
        timeframe: Literal["15m", "1h", "4h", "1d"],
        lookback: Annotated[int, Field(ge=30, le=500)] = 100,
    ) -> ToolResult[CorrelationOut]:
        """Pearson correlation between `symbol` and BTCUSDT sobre los últimos
        `lookback` cierres en el mismo timeframe.

        Útil para ponderar bias en altcoins: si el alt está +0.9 correlado
        con BTC, su bias técnico es ~derivado y no debe tratarse como tesis
        independiente. Cita esta correlación en confluences cuando el
        símbolo NO es BTCUSDT.
        """
        symbol = symbol.upper()
        cutoff = floor_to_timeframe(datetime.now(tz=UTC), timeframe)

        # BTC vs sí mismo: skip the DB call.
        if symbol == "BTCUSDT":
            return ToolResult(
                data=CorrelationOut(
                    symbol=symbol,
                    reference="BTCUSDT",
                    timeframe=timeframe,
                    lookback_bars=0,
                    pearson=1.0,
                    bias_weight_factor=1.0,
                    interpretation=(
                        "Es BTC. Correlación trivialmente 1.0 — el bias técnico "
                        "ES la tesis principal."
                    ),
                ),
                provenance=Provenance(
                    source="self_reference",
                    as_of=cutoff,
                    rows=0,
                    warnings=[],
                ),
            )

        async with ctx.deps.session_factory() as session:
            sym_rows = await fetch_range(
                session,
                exchange=ctx.deps.exchange,
                symbol=symbol,
                timeframe=timeframe,
                until=cutoff,
                limit=lookback,
            )
            btc_rows = await fetch_range(
                session,
                exchange=ctx.deps.exchange,
                symbol="BTCUSDT",
                timeframe=timeframe,
                until=cutoff,
                limit=lookback,
            )

        if len(sym_rows) < 30 or len(btc_rows) < 30:
            return ToolResult(
                data=CorrelationOut(
                    symbol=symbol,
                    reference="BTCUSDT",
                    timeframe=timeframe,
                    lookback_bars=min(len(sym_rows), len(btc_rows)),
                    pearson=0.0,
                    bias_weight_factor=0.0,
                    interpretation=(
                        f"Datos insuficientes para correlación robusta "
                        f"({min(len(sym_rows), len(btc_rows))} barras). "
                        f"Trata el bias del símbolo como independiente con cautela."
                    ),
                ),
                provenance=Provenance(
                    source=f"db.ohlcv:{EXCHANGE_NAME}:correlation:{symbol}vsBTC",
                    as_of=cutoff,
                    rows=min(len(sym_rows), len(btc_rows)),
                    warnings=["insufficient_data"],
                ),
            )

        # Alinear por timestamp: dict {ts: close} y luego intersección.
        sym_map = {r.ts: r.c for r in sym_rows}
        btc_map = {r.ts: r.c for r in btc_rows}
        common_ts = sorted(set(sym_map.keys()) & set(btc_map.keys()))
        if len(common_ts) < 30:
            rho = 0.0
            warning = ["insufficient_aligned_bars"]
        else:
            sym_closes = np.array([sym_map[t] for t in common_ts], dtype=np.float64)
            btc_closes = np.array([btc_map[t] for t in common_ts], dtype=np.float64)
            # Pearson sobre returns, no precios — más estable.
            sym_ret = np.diff(sym_closes) / sym_closes[:-1]
            btc_ret = np.diff(btc_closes) / btc_closes[:-1]
            if sym_ret.std() == 0 or btc_ret.std() == 0:
                rho = 0.0
                warning = ["zero_variance"]
            else:
                rho = float(np.corrcoef(sym_ret, btc_ret)[0, 1])
                warning = []

        weight, interp = _interpret_correlation(rho, symbol)

        ctx.deps.log.info(
            "tool.get_btc_correlation",
            symbol=symbol,
            timeframe=timeframe,
            rho=round(rho, 4),
            n_aligned=len(common_ts),
        )
        return ToolResult(
            data=CorrelationOut(
                symbol=symbol,
                reference="BTCUSDT",
                timeframe=timeframe,
                lookback_bars=len(common_ts),
                pearson=round(rho, 4),
                bias_weight_factor=round(weight, 2),
                interpretation=interp,
            ),
            provenance=Provenance(
                source=f"db.ohlcv:{EXCHANGE_NAME}:correlation:{symbol}vsBTC",
                as_of=cutoff,
                rows=len(common_ts),
                warnings=warning,
            ),
        )
