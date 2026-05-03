"""run_cpcv tool — Combinatorial Purged Cross-Validation."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field
from pydantic_ai import Agent, RunContext

from app.agent.deps import AgentDeps
from app.agent.tools._envelope import Provenance, ToolResult
from app.backtest import BacktestSpec
from app.backtest import run_cpcv as _run_cpcv
from app.backtest.strategies import STRATEGY_REGISTRY


def register_cpcv_tool(agent: Agent[AgentDeps, object]) -> None:
    @agent.tool
    async def run_cpcv(
        ctx: RunContext[AgentDeps],
        strategy_id: str,
        symbol: str,
        timeframe: Literal["15m", "1h", "4h", "1d"],
        since: datetime,
        until: datetime | None = None,
        params: dict[str, Any] | None = None,
        n_folds: Annotated[int, Field(ge=4, le=20)] = 10,
        n_test_folds: Annotated[int, Field(ge=1, le=8)] = 2,
        embargo_size: Annotated[int, Field(ge=0, le=50)] = 5,
        purged_size: Annotated[int, Field(ge=0, le=50)] = 5,
        fees_bps: float = 4.0,
        slippage_atr: float = 0.05,
    ) -> ToolResult[dict[str, Any]]:
        """Combinatorial Purged Cross-Validation (López de Prado).

        Carves the equity curve into N=n_folds blocks, evaluates Sharpe across
        all combinatorial test-fold subsets, and reports a DISTRIBUTION of
        Sharpes plus the Probability of Backtest Overfitting (PBO).

        A single Sharpe number is fundamentally misleading; CPCV gives you
        the distribution that single number was sampled from. If the median
        Sharpe is positive but the 25th percentile is negative — the strategy
        is fragile and likely overfit.
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
            result = await _run_cpcv(
                session,
                base_spec=spec,
                n_folds=n_folds,
                n_test_folds=n_test_folds,
                embargo_size=embargo_size,
                purged_size=purged_size,
                exchange=ctx.deps.exchange,
            )

        ctx.deps.log.info(
            "tool.run_cpcv",
            strategy=strategy_id,
            n_paths=result.n_paths,
            sharpe_p50=result.sharpe_p50,
            dsr=result.deflated_sharpe,
            pbo=result.pbo,
        )
        return ToolResult(
            data={
                "strategy_id": strategy_id,
                "n_paths": result.n_paths,
                "sharpe_distribution": result.sharpe_distribution,
                "sharpe_mean": result.sharpe_mean,
                "sharpe_p25": result.sharpe_p25,
                "sharpe_p50": result.sharpe_p50,
                "sharpe_p75": result.sharpe_p75,
                "deflated_sharpe": result.deflated_sharpe,
                "pbo": result.pbo,
                "overfit_warning": result.overfit_warning,
            },
            provenance=Provenance(
                source=f"db.backtest_runs:cpcv:{strategy_id}",
                as_of=until or datetime.now(),
                rows=result.n_paths,
                warnings=(
                    [f"DSR={result.deflated_sharpe} < 0.5 OR PBO={result.pbo} > 0.5: overfit"]
                    if result.overfit_warning
                    else []
                ),
            ),
        )
