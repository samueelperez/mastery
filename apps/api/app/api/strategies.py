"""Read-only endpoint para el registry de estrategias.

GET /strategies/registry — lista cada strategy_id con su `description`
legible y los `default_params`. Lo consume el frontend para renderizar
"qué hace esta estrategia" en `/research/backtests/[id]` sin tener que
duplicar el copy en TS.

Sin auth: info pública del producto. La cache se aplica con
`Cache-Control: max-age=300` (5 min) — el registry sólo cambia con un
deploy, así que es seguro.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel

from app.backtest.strategies import STRATEGY_REGISTRY

# Importar los módulos de estrategias asegura que sus decoradores
# `@register` se ejecuten y pueblen STRATEGY_REGISTRY. Sin estos imports
# el registry está vacío al levantar el módulo independiente.
import app.backtest.strategies.bollinger_reversion  # noqa: F401
import app.backtest.strategies.ema_cross  # noqa: F401

router = APIRouter()


class StrategyRegistryRow(BaseModel):
    id: str
    name: str
    description: str
    default_params: dict[str, Any]


@router.get(
    "/strategies/registry",
    response_model=list[StrategyRegistryRow],
    tags=["research"],
)
async def list_strategy_registry(response: Response) -> list[StrategyRegistryRow]:
    response.headers["Cache-Control"] = "public, max-age=300"
    return [
        StrategyRegistryRow(
            id=s.id,
            name=s.name,
            description=s.description,
            default_params=s.default_params,
        )
        for s in STRATEGY_REGISTRY.values()
    ]
