"""run_backtest tool — single backtest run, persists to backtest_runs."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field
from pydantic_ai import Agent, RunContext

from app.agent.deps import AgentDeps
from app.agent.tools._envelope import Provenance, ToolResult
from app.backtest import BacktestSpec
from app.backtest import run_backtest as _run_backtest
from app.backtest.strategies import STRATEGY_REGISTRY


def register_run_backtest_tool(agent: Agent[AgentDeps, object]) -> None:
    @agent.tool
    async def run_backtest(
        ctx: RunContext[AgentDeps],
        strategy_id: str,
        symbol: str,
        timeframe: Literal["15m", "1h", "4h", "1d"],
        since: datetime,
        until: datetime | None = None,
        params: dict[str, Any] | None = None,
        fees_bps: Annotated[float, Field(ge=0, le=100)] = 4.0,
        slippage_atr: Annotated[float, Field(ge=0, le=1)] = 0.05,
        initial_equity: Annotated[float, Field(gt=0)] = 10_000.0,
    ) -> ToolResult[dict[str, Any]]:
        """Run a single backtest. `strategy_id` must be one of the registered
        strategies; call get_strategy_metrics() with no run_id to discover them.

        Default fees: 4 bps (Binance USDT-M taker). Default slippage: 5% of ATR.

        Persists the result to `backtest_runs` and returns a summary with
        run_id (for citation), key metrics (Sharpe, DSR, max DD, expectancy R),
        and the overfit_warning flag.
        """
        if strategy_id not in STRATEGY_REGISTRY:
            return ToolResult(
                data={
                    "error": "unknown strategy",
                    "known_strategies": sorted(STRATEGY_REGISTRY.keys()),
                },
                provenance=Provenance(
                    source="strategies", as_of=since, rows=0,
                    warnings=[f"strategy_id {strategy_id!r} not registered"],
                ),
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
            initial_equity=initial_equity,
        )

        async with ctx.deps.session_factory() as session:
            result = await _run_backtest(session, spec=spec, exchange=ctx.deps.exchange)

        m = result.metrics
        ctx.deps.log.info(
            "tool.run_backtest",
            run_id=result.run_id,
            strategy=strategy_id,
            n_trades=m.n_trades,
            sharpe=m.sharpe,
            dsr=m.deflated_sharpe,
            overfit_warning=m.overfit_warning,
        )
        return ToolResult(
            data={
                "run_id": result.run_id,
                "strategy_id": strategy_id,
                "n_trades": m.n_trades,
                "sharpe": m.sharpe,
                "sortino": m.sortino,
                "deflated_sharpe": m.deflated_sharpe,
                "probabilistic_sharpe": m.probabilistic_sharpe,
                "max_drawdown": m.max_drawdown,
                "max_drawdown_duration_bars": m.max_drawdown_duration_bars,
                "calmar": m.calmar,
                "ulcer_index": m.ulcer_index,
                "expectancy_R": m.expectancy_R,
                "win_rate": m.win_rate,
                "overfit_warning": m.overfit_warning,
            },
            provenance=Provenance(
                source=f"db.backtest_runs:{result.run_id}",
                as_of=until or datetime.now(),
                rows=m.n_trades,
                warnings=(
                    ["DSR < 0.5: result likely overfit"] if m.overfit_warning else []
                ),
            ),
        )
