"""get_strategy_metrics tool — read backtest_runs / strategy_metrics."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic_ai import Agent, RunContext
from sqlalchemy import text

from app.agent.deps import AgentDeps
from app.agent.tools._envelope import Provenance, ToolResult
from app.backtest.strategies import STRATEGY_REGISTRY


def register_strategy_metrics_tool(agent: Agent[AgentDeps, object]) -> None:
    @agent.tool
    async def get_strategy_metrics(
        ctx: RunContext[AgentDeps],
        strategy_id: str | None = None,
        run_id: str | None = None,
    ) -> ToolResult[dict[str, Any]]:
        """Read strategy registry + recent backtest runs.

        - No args → list all registered strategies + their default params.
        - `strategy_id` → return last run + aggregate stats for that strategy.
        - `run_id` → return the full row (params, metrics, equity_curve summary).

        The agent MUST cite this tool when claiming historical performance
        ("EMA cross hizo Sharpe 1.4 en backtest"). The cited run_id IS the receipt.
        """
        # Discovery mode: no args
        if strategy_id is None and run_id is None:
            data = {
                "strategies": [
                    {
                        "id": s.id,
                        "description": s.description,
                        "default_params": s.default_params,
                    }
                    for s in STRATEGY_REGISTRY.values()
                ]
            }
            return ToolResult(
                data=data,
                provenance=Provenance(
                    source="strategies.registry",
                    as_of=datetime.now(),
                    rows=len(STRATEGY_REGISTRY),
                ),
            )

        async with ctx.deps.session_factory() as session:
            if run_id is not None:
                row = (
                    await session.execute(
                        text(
                            """
                            SELECT id, strategy_id, params, symbol, timeframe,
                                   range_start, range_end, fees_bps, slippage_atr,
                                   metrics, status, created_at
                            FROM backtest_runs
                            WHERE id = CAST(:rid AS uuid)
                            """
                        ),
                        {"rid": run_id},
                    )
                ).mappings().one_or_none()
                if not row:
                    return ToolResult(
                        data={"error": "run not found", "run_id": run_id},
                        provenance=Provenance(
                            source=f"db.backtest_runs:{run_id}",
                            as_of=datetime.now(),
                            rows=0,
                            warnings=[f"run_id {run_id} not in backtest_runs"],
                        ),
                    )
                return ToolResult(
                    data={"run": dict(row)},
                    provenance=Provenance(
                        source=f"db.backtest_runs:{run_id}",
                        as_of=row["range_end"],
                        rows=1,
                    ),
                )

            # strategy_id: aggregate + last run
            agg = (
                await session.execute(
                    text(
                        """
                        SELECT strategy_id, last_run_id, n_runs, best_dsr, last_updated
                        FROM strategy_metrics
                        WHERE strategy_id = :sid
                        """
                    ),
                    {"sid": strategy_id},
                )
            ).mappings().one_or_none()
            recent = (
                await session.execute(
                    text(
                        """
                        SELECT id, params, symbol, timeframe, range_start, range_end,
                               metrics, created_at
                        FROM backtest_runs
                        WHERE strategy_id = :sid
                        ORDER BY created_at DESC
                        LIMIT 5
                        """
                    ),
                    {"sid": strategy_id},
                )
            ).mappings().all()

            return ToolResult(
                data={
                    "strategy_id": strategy_id,
                    "aggregate": dict(agg) if agg else None,
                    "recent_runs": [dict(r) for r in recent],
                    "default_params": STRATEGY_REGISTRY[strategy_id].default_params
                    if strategy_id in STRATEGY_REGISTRY
                    else None,
                },
                provenance=Provenance(
                    source=f"db.backtest_runs:{strategy_id}",
                    as_of=datetime.now(),
                    rows=len(recent),
                    warnings=(
                        [f"no runs yet for {strategy_id}"] if not recent else []
                    ),
                ),
            )
