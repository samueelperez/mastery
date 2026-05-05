"""Strategy performance metrics — Sharpe, Sortino, MAR, Calmar, Ulcer, max DD,
expectancy in R, plus Bailey & López de Prado's Probabilistic / Deflated Sharpe
and the Probability of Backtest Overfitting (PBO).

References:
- Bailey & López de Prado, "The Sharpe Ratio Efficient Frontier" (2012) — PSR
- Bailey & López de Prado, "The Deflated Sharpe Ratio" (2014) — DSR
- Bailey, Borwein, López de Prado, Zhu, "The Probability of Backtest Overfitting" (2016) — PBO

All formulas implemented in numpy from the papers — no external lib needed.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

# -----------------------------------------------------------------------------
# Pydantic schema (serialized into backtest_runs.metrics jsonb)
# -----------------------------------------------------------------------------

ANNUALIZATION_FACTOR_DEFAULT = math.sqrt(252)  # daily; runner overrides per-tf


# Crypto trades 24/7 — no exchange close — so we annualize on actual hours.
_BARS_PER_YEAR: dict[str, float] = {
    "1m": 60 * 24 * 365,
    "5m": 12 * 24 * 365,
    "15m": 4 * 24 * 365,
    "1h": 24 * 365,
    "4h": 6 * 365,
    "1d": 365,
}


def annualization_factor_for(timeframe: str) -> float:
    """sqrt(bars_per_year) for the given timeframe; falls back to √252 (daily-equiv)."""
    return math.sqrt(_BARS_PER_YEAR.get(timeframe, 252))


class StrategyMetrics(BaseModel):
    """All numbers the agent and UI cite. JSON-friendly."""

    n_trades: int
    win_rate: float = Field(..., ge=0.0, le=1.0)
    avg_win_R: float
    avg_loss_R: float
    expectancy_R: float
    sharpe: float
    sortino: float | None = Field(
        default=None,
        description="None when there are no losing returns (sortino would be infinite).",
    )
    max_drawdown: float = Field(..., ge=0.0, le=1.0)
    max_drawdown_duration_bars: int
    calmar: float
    mar: float
    ulcer_index: float
    tail_ratio: float
    skew: float
    kurtosis: float

    # López de Prado family
    probabilistic_sharpe: float = Field(..., ge=0.0, le=1.0)  # P(SR > 0) under returns dist
    deflated_sharpe: float                                     # Sharpe deflated by N trials + non-normality
    overfit_warning: bool                                      # DSR < 0.5 OR PBO > 0.5
    probability_of_overfit: float | None = None                # populated only by run_cpcv


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _equity_to_returns(equity: list[float]) -> np.ndarray:
    e = np.asarray(equity, dtype=np.float64)
    if e.size < 2:
        return np.array([], dtype=np.float64)
    rets = np.diff(e) / e[:-1]
    return rets


def _sharpe_from_returns(rets: np.ndarray, ann: float = ANNUALIZATION_FACTOR_DEFAULT) -> float:
    if rets.size < 2 or rets.std(ddof=1) == 0:
        return 0.0
    return float(rets.mean() / rets.std(ddof=1) * ann)


def _sortino_from_returns(
    rets: np.ndarray, ann: float = ANNUALIZATION_FACTOR_DEFAULT
) -> float | None:
    """Return None when there are no losing returns (sortino is then undefined)."""
    if rets.size < 2:
        return 0.0
    downside = rets[rets < 0]
    if downside.size == 0:
        return None
    dd = downside.std(ddof=1)
    if dd == 0:
        return 0.0
    return float(rets.mean() / dd * ann)


def _max_dd(equity: np.ndarray) -> tuple[float, int]:
    """Returns (max_drawdown_fraction, max_dd_duration_in_bars)."""
    if equity.size < 2:
        return 0.0, 0
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak  # negative
    max_dd_frac = float(-dd.min()) if dd.size else 0.0
    # duration: longest run between peak and recovery
    in_dd = dd < 0
    durations: list[int] = []
    cur = 0
    for d in in_dd:
        if d:
            cur += 1
        else:
            if cur > 0:
                durations.append(cur)
            cur = 0
    if cur > 0:
        durations.append(cur)
    max_dd_dur = max(durations) if durations else 0
    return max_dd_frac, max_dd_dur


def _ulcer_index(equity: np.ndarray) -> float:
    if equity.size < 2:
        return 0.0
    peak = np.maximum.accumulate(equity)
    dd_pct = (equity - peak) / peak * 100.0
    return float(np.sqrt((dd_pct ** 2).mean()))


def _tail_ratio(rets: np.ndarray) -> float:
    if rets.size < 20:
        return 0.0
    p95 = np.percentile(rets, 95)
    p5 = np.percentile(rets, 5)
    if p5 == 0:
        return 0.0
    return float(abs(p95 / p5))


def _skew(rets: np.ndarray) -> float:
    if rets.size < 3:
        return 0.0
    m = rets.mean()
    s = rets.std(ddof=1)
    if s == 0:
        return 0.0
    return float(((rets - m) ** 3).mean() / s ** 3)


def _kurt_excess(rets: np.ndarray) -> float:
    if rets.size < 4:
        return 0.0
    m = rets.mean()
    s = rets.std(ddof=1)
    if s == 0:
        return 0.0
    return float(((rets - m) ** 4).mean() / s ** 4 - 3.0)


def _per_trade_returns(trades: list[dict[str, Any]], initial_equity: float) -> np.ndarray:
    """Retornos % por trade (pnl / capital_at_entry).

    Para PSR/DSR usamos esto en lugar de retornos por bar — cada trade es
    aproximadamente independiente; los retornos por bar están altamente
    autocorrelacionados durante una posición abierta (mark-to-market) y
    sesgan al alza la confianza estadística (auditoría 2026-05 #B11).

    Aproximamos `capital_at_entry` con `initial_equity` cuando no se conoce
    explícitamente. Es un proxy conservador: tras compounding, la posición
    se dimensiona sobre el equity vigente, así que el % return per trade
    es el mismo que se ve en el simulador. """
    if not trades:
        return np.array([])
    rs: list[float] = []
    for t in trades:
        pnl = t.get("pnl")
        if pnl is None:
            continue
        # Aproximación: % return = pnl / initial_equity. Es el shape correcto
        # bajo la hipótesis de que cada trade dimensiona ≈igual; con
        # compounding real la magnitud absoluta de pnl crece, pero el ratio
        # mean/std no cambia (homogeneidad por escala).
        rs.append(float(pnl) / initial_equity)
    return np.array(rs, dtype=np.float64)


# -----------------------------------------------------------------------------
# López de Prado family
# -----------------------------------------------------------------------------


def probabilistic_sharpe(
    sharpe: float, n: int, *, skew: float = 0.0, kurt_excess: float = 0.0, sr_benchmark: float = 0.0
) -> float:
    """PSR(SR*) = Pr[SR > SR*].

    From Bailey & López de Prado (2012), based on Mertens (2002) / Christie (2005):
        Var(SR) = (1 + (1/2)·SR² - γ3·SR + (γ4-3)/4·SR²) / N

    where γ3 = skew, γ4 = kurtosis. With kurt_excess = γ4 - 3:
        Var(SR) = (1 + 0.5·SR² - skew·SR + kurt_excess/4·SR²) / (N-1)
    """
    if n < 3:
        return 0.5
    sr_sq = sharpe * sharpe
    denom_sq = 1.0 + 0.5 * sr_sq - skew * sharpe + kurt_excess / 4.0 * sr_sq
    if denom_sq <= 0:
        return 0.5
    sr_std = math.sqrt(denom_sq / (n - 1))
    if sr_std == 0:
        return 0.5
    z = (sharpe - sr_benchmark) / sr_std
    return float(_normal_cdf(z))


def deflated_sharpe(
    sharpe: float, n: int, *, n_trials: int, skew: float = 0.0, kurt_excess: float = 0.0
) -> float:
    """DSR adjusts the PSR benchmark for the # of strategy variations tried.

    From Bailey & López de Prado (2014): DSR = PSR(SR > E[max SR over N trials]).
    Approximate the expected max with the standard extreme-value formula:
        E[max] ≈ sqrt(2 * ln(n_trials)) - (1 - γ) / sqrt(2 * ln(n_trials))
    where γ ≈ 0.5772 is the Euler-Mascheroni constant.
    """
    if n_trials < 1:
        return probabilistic_sharpe(sharpe, n, skew=skew, kurt_excess=kurt_excess)
    if n_trials == 1:
        sr_benchmark = 0.0
    else:
        ln_n = math.log(max(n_trials, 2))
        sr_benchmark = math.sqrt(2 * ln_n) - (1 - 0.5772156649) / math.sqrt(2 * ln_n)
    return probabilistic_sharpe(sharpe, n, skew=skew, kurt_excess=kurt_excess, sr_benchmark=sr_benchmark)


def probability_of_overfit(in_sample_ranks: list[int], out_of_sample_ranks: list[int]) -> float:
    """PBO from Bailey/Borwein/López de Prado (2016).

    Given paired (IS rank, OOS rank) for the best in-sample strategy across
    folds: PBO = fraction of folds where the IS-best ranked below median OOS.
    Inputs are 1-based ranks.
    """
    if len(in_sample_ranks) != len(out_of_sample_ranks):
        raise ValueError("PBO inputs must have the same length")
    if not in_sample_ranks:
        return 0.5
    n = len(in_sample_ranks)
    median_rank = (n + 1) / 2.0
    # We get IS_best and check OOS rank — caller is expected to pass exactly
    # those pairs (one per fold). Below-median OOS = overfit signal.
    bad = sum(1 for r in out_of_sample_ranks if r > median_rank)
    return bad / n


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


# -----------------------------------------------------------------------------
# Public entrypoint
# -----------------------------------------------------------------------------


def compute_metrics(
    *,
    equity_curve: list[tuple[datetime, float]],
    trades: list[dict[str, Any]],
    initial_equity: float,
    n_trials: int = 1,
    annualization_factor: float = ANNUALIZATION_FACTOR_DEFAULT,
) -> StrategyMetrics:
    if not equity_curve:
        return StrategyMetrics(
            n_trades=0, win_rate=0.0, avg_win_R=0.0, avg_loss_R=0.0,
            expectancy_R=0.0, sharpe=0.0, sortino=0.0, max_drawdown=0.0,
            max_drawdown_duration_bars=0, calmar=0.0, mar=0.0, ulcer_index=0.0,
            tail_ratio=0.0, skew=0.0, kurtosis=0.0, probabilistic_sharpe=0.5,
            deflated_sharpe=0.0, overfit_warning=True,
        )
    equity_vals = np.array([e for _, e in equity_curve], dtype=np.float64)
    rets = _equity_to_returns(equity_vals.tolist())

    sharpe = _sharpe_from_returns(rets, ann=annualization_factor)
    sortino = _sortino_from_returns(rets, ann=annualization_factor)
    max_dd, max_dd_dur = _max_dd(equity_vals)
    ulcer = _ulcer_index(equity_vals)
    tail = _tail_ratio(rets)
    sk = _skew(rets)
    ku = _kurt_excess(rets)

    # PSR/DSR usan Sharpe per-trade y n=n_trades (auditoría 2026-05 #B11).
    # Los retornos por bar están autocorrelacionados durante posiciones
    # abiertas (mark-to-market); usar `n=len(bars)` infla la confianza
    # estadística 2-3×. Cada trade es ≈independiente — esa es la N efectiva
    # que usan Bailey & López de Prado en AFML §13.2.
    trade_rets = _per_trade_returns(trades, initial_equity)
    if trade_rets.size >= 3:
        sharpe_per_trade = float(trade_rets.mean() / trade_rets.std(ddof=1))
        if not math.isfinite(sharpe_per_trade):
            sharpe_per_trade = 0.0
        sk_trades = _skew(trade_rets)
        ku_trades = _kurt_excess(trade_rets)
        n_eff = trade_rets.size
        psr = probabilistic_sharpe(
            sharpe_per_trade, n_eff,
            skew=sk_trades, kurt_excess=ku_trades, sr_benchmark=0.0,
        )
        dsr = deflated_sharpe(
            sharpe_per_trade, n_eff, n_trials=n_trials,
            skew=sk_trades, kurt_excess=ku_trades,
        )
    else:
        # Sin trades suficientes (cold start o estrategia que no dispara) —
        # neutral por convención (igual que probabilistic_sharpe(n<3)).
        psr = 0.5
        dsr = 0.0

    # Trade-level stats
    if trades:
        rs = [t["r_multiple"] for t in trades if t.get("r_multiple") is not None]
        wins = [r for r in rs if r > 0]
        losses = [r for r in rs if r < 0]
        win_rate = len(wins) / len(rs) if rs else 0.0
        avg_win = float(np.mean(wins)) if wins else 0.0
        avg_loss = float(np.mean(losses)) if losses else 0.0
        expectancy = float(np.mean(rs)) if rs else 0.0
    else:
        win_rate = avg_win = avg_loss = expectancy = 0.0

    # CAGR / MAR. Use the equity curve span if it has timestamps.
    if len(equity_curve) >= 2:
        days = (equity_curve[-1][0] - equity_curve[0][0]).total_seconds() / 86400.0
        years = max(days / 365.25, 1e-9)
        total_return = equity_vals[-1] / initial_equity - 1.0
        cagr = (1.0 + total_return) ** (1.0 / years) - 1.0 if total_return > -1.0 else -1.0
    else:
        cagr = 0.0

    calmar = cagr / max_dd if max_dd > 0 else 0.0
    mar = calmar  # same definition for our purposes

    overfit = (dsr < 0.5) or (max_dd > 0.5)

    return StrategyMetrics(
        n_trades=len(trades),
        win_rate=round(win_rate, 4),
        avg_win_R=round(avg_win, 4),
        avg_loss_R=round(avg_loss, 4),
        expectancy_R=round(expectancy, 4),
        sharpe=round(sharpe, 4),
        sortino=round(sortino, 4) if sortino is not None else None,
        max_drawdown=round(max_dd, 4),
        max_drawdown_duration_bars=max_dd_dur,
        calmar=round(calmar, 4),
        mar=round(mar, 4),
        ulcer_index=round(ulcer, 4),
        tail_ratio=round(tail, 4),
        skew=round(sk, 4),
        kurtosis=round(ku, 4),
        probabilistic_sharpe=round(psr, 4),
        deflated_sharpe=round(dsr, 4),
        overfit_warning=overfit,
    )
