"""Indicator panel: compute multiple indicators on a candle window in a single pass.

Used by `tools/indicators.py` to fulfil `get_indicators(symbol, timeframe, [specs])`
in one DB roundtrip + one Polars `collect()`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

import polars as pl
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.market.indicators.core import atr, ema, rsi, sma
from app.market.indicators.momentum import bbands, macd
from app.market.indicators.trend import adx
from app.market.indicators.volume import vwap
from app.market.ohlcv.repo import fetch_range

IndicatorName = Literal["sma", "ema", "rsi", "atr", "macd", "bbands", "adx", "vwap"]


class IndicatorSpec(BaseModel):
    """Declarative request for one indicator on a panel.

    `length` is required for length-based indicators (sma/ema/rsi/atr/bbands/adx);
    optional for macd (uses 12/26/9 default) and vwap (uses session anchor default).
    Defaults match the conventions used by Wilder, Bollinger and Murphy.
    """

    name: IndicatorName
    length: int | None = Field(default=None, ge=2, le=500)
    source: Literal["o", "h", "l", "c"] = "c"


_DEFAULT_LENGTHS: dict[IndicatorName, int] = {
    "sma": 20,
    "ema": 21,
    "rsi": 14,
    "atr": 14,
    "macd": 0,  # ignored; macd uses fast/slow/signal
    "bbands": 20,
    "adx": 14,
    "vwap": 0,  # ignored
}


def _grouped_suffix(spec: IndicatorSpec) -> str:
    """Sufijo único por spec para evitar colisión entre dos indicadores del
    mismo tipo con longitudes distintas (e.g. bbands(20) y bbands(50)).

    Política: usamos sufijo SÓLO cuando la longitud difiere del default. Así:
      - bbands() o bbands(20)  → cols `bb_mid`, `bb_upper`...        (default)
      - bbands(50)             → cols `bb_mid_50`, `bb_upper_50`...  (sufijo)
      - bbands(20) + bbands(50) en una llamada → uno por column-set, sin
        colisión.

    Mantiene backward compat con tests/clientes que esperan los nombres
    "limpios" cuando no hay parametrización. """
    if spec.length is None:
        return ""
    default = _DEFAULT_LENGTHS.get(spec.name, 0)
    if spec.length == default:
        return ""
    return f"_{spec.length}"


def grouped_columns(spec: IndicatorSpec) -> list[str]:
    """Devuelve las columnas que producirá este spec en el panel.
    Usado por `agent/tools/indicators.py` para mapear cada spec a sus
    columnas en el `latest` snapshot. """
    suf = _grouped_suffix(spec)
    match spec.name:
        case "macd":
            # macd no parametriza longitud, sólo fast/slow/signal default.
            return ["macd", "macd_signal", "macd_hist"]
        case "bbands":
            return [f"bb_mid{suf}", f"bb_upper{suf}", f"bb_lower{suf}", f"bb_bw{suf}"]
        case "adx":
            return [f"adx{suf}", f"plus_di{suf}", f"minus_di{suf}"]
        case "vwap":
            return ["vwap"]
        case _:
            length = spec.length if spec.length is not None else _DEFAULT_LENGTHS[spec.name]
            return [f"{spec.name}_{length}"]


def _apply(lf: pl.LazyFrame, spec: IndicatorSpec) -> pl.LazyFrame:
    length = spec.length if spec.length is not None else _DEFAULT_LENGTHS[spec.name]
    suf = _grouped_suffix(spec)
    match spec.name:
        case "sma":
            return sma(lf, length=length, source=spec.source)
        case "ema":
            return ema(lf, length=length, source=spec.source)
        case "rsi":
            return rsi(lf, length=length, source=spec.source)
        case "atr":
            return atr(lf, length=length)
        case "macd":
            return macd(lf, source=spec.source)
        case "bbands":
            return bbands(
                lf,
                length=length,
                source=spec.source,
                out_mid=f"bb_mid{suf}",
                out_upper=f"bb_upper{suf}",
                out_lower=f"bb_lower{suf}",
                out_bw=f"bb_bw{suf}",
            )
        case "adx":
            return adx(
                lf,
                length=length,
                out_adx=f"adx{suf}",
                out_plus_di=f"plus_di{suf}",
                out_minus_di=f"minus_di{suf}",
            )
        case "vwap":
            return vwap(lf, anchor="session")


async def compute_panel(
    session: AsyncSession,
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
    lookback: int,
    specs: list[IndicatorSpec],
    until: datetime | None = None,
) -> pl.DataFrame:
    """Fetch `lookback` candles ending at `until` (or now) and compute every spec.

    Returns a `pl.DataFrame` (eagerly collected) with the OHLCV columns plus one or
    more columns per indicator. Rows are oldest-first.
    """
    rows = await fetch_range(
        session,
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        until=until,
        limit=lookback,
    )
    if not rows:
        return pl.DataFrame(
            schema={"ts": pl.Datetime("us", "UTC"), "o": pl.Float64, "h": pl.Float64,
                    "l": pl.Float64, "c": pl.Float64, "v": pl.Float64}
        )

    lf = pl.LazyFrame(
        {
            "ts": [r.ts for r in rows],
            "o": [r.o for r in rows],
            "h": [r.h for r in rows],
            "l": [r.l for r in rows],
            "c": [r.c for r in rows],
            "v": [r.v for r in rows],
        }
    )
    for spec in specs:
        lf = _apply(lf, spec)
    return lf.collect()
