"""run_walk_forward tool — multi-fold OOS evaluation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import Field
from pydantic_ai import Agent, RunContext

from app.agent.deps import AgentDeps
from app.agent.tools._envelope import Provenance, ToolResult
from app.backtest import BacktestSpec
from app.backtest import run_walk_forward as _run_walk_forward
from app.backtest.strategies import STRATEGY_REGISTRY


def register_walk_forward_tool(agent: Agent[AgentDeps, object]) -> None:
    @agent.tool
    async def run_walk_forward(
        ctx: RunContext[AgentDeps],
        strategy_id: str,
        symbol: str,
        timeframe: Literal["15m", "1h", "4h", "1d"],
        since: datetime,
        until: datetime | None = None,
        params: dict[str, Any] | None = None,
        is_months: Annotated[int, Field(ge=1, le=36)] = 12,
        oos_months: Annotated[int, Field(ge=1, le=12)] = 3,
        embargo_days: Annotated[int, Field(ge=0, le=14)] = 1,
        fees_bps: float = 4.0,
        slippage_atr: float = 0.05,
    ) -> ToolResult[dict[str, Any]]:
        """Walk-forward analysis: split [since, until] into rolling
        (in-sample, out-of-sample) windows, measure OOS performance only.

        F2 does NOT re-optimize per fold (the agent picks params once); the
        purpose here is to detect when an edge is front-loaded vs persistent.
        """
        if strategy_id not in STRATEGY_REGISTRY:
            return ToolResult(
                data={"error": "unknown strategy", "known": sorted(STRATEGY_REGISTRY.keys())},
                provenance=Provenance(source="strategies", as_of=since, rows=0, warnings=[]),
            )

        spec = BacktestSpec(
            strategy_id=strategy_id,
            params=params or {},
            symbol=symbol.upper(),
            timeframe=timeframe,
            since=since,
            until=until,
            fees_bps=fees_bps,
            slippage_atr=slippage_atr,
        )
        async with ctx.deps.session_factory() as session:
            result = await _run_walk_forward(
                session,
                base_spec=spec,
                user_id=ctx.deps.user_id,
                is_months=is_months,
                oos_months=oos_months,
                embargo_days=embargo_days,
                exchange=ctx.deps.exchange,
            )

        agg = result.aggregate_oos_metrics
        ctx.deps.log.info(
            "tool.run_walk_forward",
            strategy=strategy_id,
            n_folds=len(result.folds),
            avg_dsr=agg.deflated_sharpe,
            n_total_trades=agg.n_trades,
        )
        return ToolResult(
            data={
                "strategy_id": strategy_id,
                "n_folds": len(result.folds),
                "folds": [
                    {
                        "fold": f.fold,
                        "oos_start": f.out_sample_start.isoformat(),
                        "oos_end": f.out_sample_end.isoformat(),
                        "sharpe": f.metrics.sharpe,
                        "dsr": f.metrics.deflated_sharpe,
                        "max_dd": f.metrics.max_drawdown,
                        "n_trades": f.n_trades,
                    }
                    for f in result.folds
                ],
                "aggregate_oos": {
                    "n_trades": agg.n_trades,
                    "avg_sharpe": agg.sharpe,
                    "avg_dsr": agg.deflated_sharpe,
                    "worst_max_dd": agg.max_drawdown,
                    "overfit_warning": agg.overfit_warning,
                },
            },
            provenance=Provenance(
                source=f"db.backtest_runs:wf:{strategy_id}",
                as_of=until or datetime.now(tz=UTC),
                rows=len(result.folds),
                warnings=(
                    ["aggregate avg_dsr < 0.5: edge not persistent across folds"]
                    if agg.overfit_warning
                    else []
                ),
            ),
        )
