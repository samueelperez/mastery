"""Re-export shim post-PR12 layering fix.

Canonical home: `app.core.time`. Existing imports en agent/tools/* siguen
funcionando hasta que se migren — el fix de layering movió las funciones a
core/ para que `market/ohlcv/*` y `backtest/` no importen desde `agent/`.
"""

from app.core.time import floor_to_timeframe, staleness_warning

__all__ = ["floor_to_timeframe", "staleness_warning"]
