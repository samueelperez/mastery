"""Paper trading engine (F4).

Two layers:
- `engine.py` — pure helpers: `simulate_fill` (slippage), `compute_funding_cost_bps`.
- `positions.py` — stateful: balance, positions, equity snapshots (uses Decimal).
- `repo.py`     — paper_fills ledger for slippage calibration (legacy F0-F3).
"""

from app.paper_trading.engine import (
    FillSimulationInput,
    FillSimulationOutput,
    compute_funding_cost_bps,
    simulate_fill,
)
from app.paper_trading.positions import (
    PaperPositionRow,
    close_position,
    get_balance,
    get_equity_curve,
    get_open_positions,
    init_balance,
    open_position,
    partial_close_position,
    snapshot_equity,
)

__all__ = [
    "FillSimulationInput",
    "FillSimulationOutput",
    "PaperPositionRow",
    "close_position",
    "compute_funding_cost_bps",
    "get_balance",
    "get_equity_curve",
    "get_open_positions",
    "init_balance",
    "open_position",
    "partial_close_position",
    "simulate_fill",
    "snapshot_equity",
]
