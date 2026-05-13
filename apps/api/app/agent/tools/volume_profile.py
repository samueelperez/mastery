"""get_volume_profile tool — volume by price (POC / HVN / LVN).

Idea: en lugar de mirar el volumen a lo largo del TIEMPO (lo que muestra
una columna de volumen clásica), distribuirlo a lo largo del PRECIO. Cada
nivel de precio acumula el volumen de las velas cuyo rango (low..high) lo
contuvo. El resultado es un histograma horizontal que materializa "dónde
hubo aceptación de mercado":

  POC (Point of Control) — el bin con MÁS volumen acumulado. Equilibrio
                            del rango analizado, soporte/resistencia mayor.
  HVN (High Volume Nodes) — bins con volumen >70% del POC. Zonas de
                            consolidación; el precio tiende a permanecer
                            cuando los toca.
  LVN (Low Volume Nodes) — bins con volumen <30% del POC. "Vacíos" donde
                           el precio se mueve rápido (rejection / breakout
                           targets).

Útil como complement a S/R clásico (que viene de pivots): VP captura la
ESTRUCTURA del volumen, S/R captura la GEOMETRÍA del precio.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

import numpy as np
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from app.agent.deps import AgentDeps
from app.agent.tools._envelope import Provenance, ToolResult
from app.agent.tools._time import floor_to_timeframe, staleness_warning
from app.market.ohlcv.repo import fetch_range


class VolumeNode(BaseModel):
    price: float  # midpoint del bin
    volume: float
    pct_of_poc: float  # 0..100


class VolumeProfileOut(BaseModel):
    symbol: str
    timeframe: str
    lookback_bars: int
    bins: int
    poc_price: float
    poc_volume: float
    high_volume_nodes: list[VolumeNode]  # ≥70% del POC
    low_volume_nodes: list[VolumeNode]  # ≤30% del POC, dentro del rango activo
    range_low: float
    range_high: float
    interpretation: str


def _interpret(
    current_close: float,
    poc: float,
    hvns: list[VolumeNode],
) -> str:
    """Una frase: dónde está el precio respecto al POC y a los HVN cercanos."""
    pct_to_poc = (current_close - poc) / poc * 100.0
    if abs(pct_to_poc) < 0.5:
        return f"Precio en el POC ({poc:.2f}) — zona de equilibrio, alta probabilidad de rotación."
    if pct_to_poc > 0:
        side = "por encima"
    else:
        side = "por debajo"
    closest_hvn = None
    if hvns:
        closest_hvn = min(hvns, key=lambda h: abs(h.price - current_close))
    if closest_hvn and abs(closest_hvn.price - current_close) / current_close < 0.005:
        return (
            f"Precio en HVN ({closest_hvn.price:.2f}, {closest_hvn.pct_of_poc:.0f}% del POC) "
            f"— zona de aceptación, soporte/resistencia local fuerte."
        )
    return (
        f"Precio {pct_to_poc:+.2f}% {side} del POC ({poc:.2f}). El POC actúa "
        f"como imán: si el precio se aleja sin volumen creciente, suele volver."
    )


def register_volume_profile_tool(agent: Agent[AgentDeps, object]) -> None:
    @agent.tool
    async def get_volume_profile(
        ctx: RunContext[AgentDeps],
        symbol: str,
        timeframe: Literal["15m", "1h", "4h", "1d"],
        lookback: Annotated[int, Field(ge=50, le=1000)] = 200,
        bins: Annotated[int, Field(ge=20, le=100)] = 50,
    ) -> ToolResult[VolumeProfileOut]:
        """Volume profile sobre los últimos `lookback` bars.

        Distribuye el volumen de cada vela uniformemente en su rango
        [low, high] y agrega en `bins` niveles de precio. Devuelve POC,
        HVN (≥70% del POC) y LVN (≤30%).

        Útil como complement a get_market_structure: el structure da pivots
        geométricos; el volume profile da niveles donde se NEGOCIÓ. Cita
        ambos para fundamentar entry/SL/TP — los HVN clusters refuerzan
        soportes/resistencias y los LVN señalan zonas de breakout rápido.
        """
        symbol = symbol.upper()
        cutoff = floor_to_timeframe(datetime.now(tz=UTC), timeframe)
        async with ctx.deps.session_factory() as session:
            rows = await fetch_range(
                session,
                exchange=ctx.deps.exchange,
                symbol=symbol,
                timeframe=timeframe,
                until=cutoff,
                limit=lookback,
            )

        if len(rows) < 30:
            return ToolResult(
                data=VolumeProfileOut(
                    symbol=symbol,
                    timeframe=timeframe,
                    lookback_bars=len(rows),
                    bins=bins,
                    poc_price=0.0,
                    poc_volume=0.0,
                    high_volume_nodes=[],
                    low_volume_nodes=[],
                    range_low=0.0,
                    range_high=0.0,
                    interpretation="Datos insuficientes para volume profile (<30 barras).",
                ),
                provenance=Provenance(
                    source=f"db.ohlcv:{ctx.deps.exchange}:{symbol}:{timeframe}",
                    as_of=cutoff,
                    rows=len(rows),
                    warnings=["insufficient_bars"],
                ),
            )

        highs = np.array([r.h for r in rows], dtype=np.float64)
        lows = np.array([r.l for r in rows], dtype=np.float64)
        volumes = np.array([r.v for r in rows], dtype=np.float64)
        current_close = float(rows[-1].c)

        range_low = float(lows.min())
        range_high = float(highs.max())
        if range_high <= range_low:
            return ToolResult(
                data=VolumeProfileOut(
                    symbol=symbol,
                    timeframe=timeframe,
                    lookback_bars=len(rows),
                    bins=bins,
                    poc_price=current_close,
                    poc_volume=0.0,
                    high_volume_nodes=[],
                    low_volume_nodes=[],
                    range_low=range_low,
                    range_high=range_high,
                    interpretation="Rango colapsado — sin perfil de volumen.",
                ),
                provenance=Provenance(
                    source=f"db.ohlcv:{ctx.deps.exchange}:{symbol}:{timeframe}",
                    as_of=cutoff,
                    rows=len(rows),
                    warnings=["zero_range"],
                ),
            )

        # Construye los bins. Cada vela contribuye su volumen UNIFORMEMENTE
        # a los bins que su rango [l, h] toca, ponderado por la fracción del
        # bin cubierta. Esta es la convención TPO/Volume-At-Price clásica.
        bin_edges = np.linspace(range_low, range_high, bins + 1)
        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2.0
        vol_bins = np.zeros(bins, dtype=np.float64)

        bin_width = (range_high - range_low) / bins
        for i in range(len(rows)):
            l, h, v = lows[i], highs[i], volumes[i]
            if h <= l or v <= 0:
                continue
            # Vela's range mapped to bin indices.
            start_idx = int((l - range_low) / bin_width)
            end_idx = int((h - range_low) / bin_width)
            start_idx = max(0, min(bins - 1, start_idx))
            end_idx = max(0, min(bins - 1, end_idx))
            n_covered = end_idx - start_idx + 1
            if n_covered <= 0:
                continue
            vol_per_bin = v / n_covered
            vol_bins[start_idx : end_idx + 1] += vol_per_bin

        poc_idx = int(np.argmax(vol_bins))
        poc_price = float(bin_centers[poc_idx])
        poc_volume = float(vol_bins[poc_idx])

        if poc_volume <= 0:
            interp = "Sin volumen agregado en el rango."
            return ToolResult(
                data=VolumeProfileOut(
                    symbol=symbol,
                    timeframe=timeframe,
                    lookback_bars=len(rows),
                    bins=bins,
                    poc_price=poc_price,
                    poc_volume=0.0,
                    high_volume_nodes=[],
                    low_volume_nodes=[],
                    range_low=range_low,
                    range_high=range_high,
                    interpretation=interp,
                ),
                provenance=Provenance(
                    source=f"db.ohlcv:{ctx.deps.exchange}:{symbol}:{timeframe}",
                    as_of=cutoff,
                    rows=len(rows),
                    warnings=["zero_volume"],
                ),
            )

        # HVN (≥70% del POC) y LVN (≤30% del POC, sólo dentro del rango
        # principal — si el bin no tuvo volumen, no es "low", es vacío).
        hvn_threshold = 0.70 * poc_volume
        lvn_threshold = 0.30 * poc_volume
        hvns: list[VolumeNode] = []
        lvns: list[VolumeNode] = []
        for i, vol in enumerate(vol_bins):
            if vol >= hvn_threshold and i != poc_idx:
                hvns.append(
                    VolumeNode(
                        price=round(float(bin_centers[i]), 4),
                        volume=round(float(vol), 4),
                        pct_of_poc=round(float(vol) / poc_volume * 100.0, 1),
                    )
                )
            elif 0 < vol <= lvn_threshold:
                lvns.append(
                    VolumeNode(
                        price=round(float(bin_centers[i]), 4),
                        volume=round(float(vol), 4),
                        pct_of_poc=round(float(vol) / poc_volume * 100.0, 1),
                    )
                )

        # Top 5 HVNs y LVNs por relevancia (HVN: más volumen; LVN: bins
        # más cercanos al POC para identificar gaps de aceptación).
        hvns.sort(key=lambda n: -n.volume)
        hvns = hvns[:5]
        lvns.sort(key=lambda n: abs(n.price - poc_price))
        lvns = lvns[:5]

        interp = _interpret(current_close, poc_price, hvns)

        last_ts = rows[-1].ts
        warnings: list[str] = []
        if w := staleness_warning(last_closed=last_ts, timeframe=timeframe):
            warnings.append(w)

        ctx.deps.log.info(
            "tool.get_volume_profile",
            symbol=symbol,
            timeframe=timeframe,
            poc=round(poc_price, 4),
            n_hvn=len(hvns),
            n_lvn=len(lvns),
        )
        return ToolResult(
            data=VolumeProfileOut(
                symbol=symbol,
                timeframe=timeframe,
                lookback_bars=len(rows),
                bins=bins,
                poc_price=round(poc_price, 4),
                poc_volume=round(poc_volume, 4),
                high_volume_nodes=hvns,
                low_volume_nodes=lvns,
                range_low=round(range_low, 4),
                range_high=round(range_high, 4),
                interpretation=interp,
            ),
            provenance=Provenance(
                source=f"db.ohlcv:{ctx.deps.exchange}:{symbol}:{timeframe}",
                as_of=last_ts,
                rows=len(rows),
                warnings=warnings,
            ),
        )
