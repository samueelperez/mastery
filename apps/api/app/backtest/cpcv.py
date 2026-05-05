"""Block bootstrap del Sharpe sobre la equity curve.

⚠️  IMPORTANTE — auditoría 2026-05:
Este módulo se llama `cpcv.py` por razones históricas pero NO implementa
CPCV (López de Prado, AFML §12.4) en el sentido estricto. CPCV honesto
exige RE-EJECUTAR la estrategia sobre cada combinación de train/test folds
para reconstruir N paths INDEPENDIENTES. Lo que hacemos aquí es:

  1. Ejecutar el backtest UNA vez sobre todo el rango.
  2. Trocear los retornos resultantes en folds y medir Sharpe en sub-slices.
  3. Reportar percentiles + un `pbo` proxy (rank in-sample vs out-of-sample
     dentro de cada par de folds, sólo 2 ranks → trivialmente ≈0.5).

**Resultado**: este `pbo` NO es el PBO de López de Prado y NO debe citarse
como tal. Por eso `compute_metrics` deja `probability_of_overfit` como `None`
hasta que F-stat-quant implemente CPCV real con re-generación de signals
por fold. `overfit_warning` se decide desde DSR (correcto) — no desde PBO.

El `sharpe_distribution` sí es informativo: muestra cómo se comporta el
Sharpe en distintas ventanas, lo que ayuda a detectar inestabilidad temporal.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import structlog
from skfolio.model_selection import CombinatorialPurgedCV
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.metrics import (
    annualization_factor_for,
    compute_metrics,
    probability_of_overfit,
)
from app.backtest.runner import BacktestSpec, run_backtest

log = structlog.get_logger(__name__)


@dataclass
class CPCVResult:
    n_paths: int
    sharpe_distribution: list[float]
    sharpe_mean: float
    sharpe_p25: float
    sharpe_p50: float
    sharpe_p75: float
    deflated_sharpe: float
    # `pbo` queda como `None` hasta que F-stat-quant implemente CPCV honesto
    # con re-generación de signals por fold. Antes era un proxy que devolvía
    # ~0.5 trivialmente y el agente lo citaba como si fuera el PBO real.
    # Ver auditoría 2026-05 / docstring del módulo.
    pbo: float | None
    overfit_warning: bool


async def run_cpcv(
    session: AsyncSession,
    *,
    base_spec: BacktestSpec,
    n_folds: int = 10,
    n_test_folds: int = 2,
    embargo_size: int = 5,
    purged_size: int = 5,
    exchange: str = "binance_usdm",
) -> CPCVResult:
    """Run a single backtest, then carve its returns into CPCV folds and
    measure Sharpe across all combinatorial test-fold combinations.

    This is faster than re-running the strategy per fold (which would be
    correct only if the strategy is path-dependent in ways our trades aren't).
    For F2's signal-based strategies, slicing the equity curve is sufficient.
    """
    full = await run_backtest(session, spec=base_spec, exchange=exchange, persist=False)
    eq = np.array([e for _, e in full.equity_curve], dtype=np.float64)
    if eq.size < n_folds * 5:
        log.warning("cpcv.too_few_bars", n_bars=eq.size, n_folds=n_folds)
        return CPCVResult(
            n_paths=0, sharpe_distribution=[], sharpe_mean=0, sharpe_p25=0,
            sharpe_p50=0, sharpe_p75=0, deflated_sharpe=0, pbo=None,
            overfit_warning=True,
        )

    rets = np.diff(eq) / eq[:-1]
    # CombinatorialPurgedCV yields (train_idx, test_idx) pairs over the index space.
    # We treat each combination as one path; Sharpe of the test slice goes into the dist.
    cv = CombinatorialPurgedCV(
        n_folds=n_folds,
        n_test_folds=n_test_folds,
        purged_size=purged_size,
        embargo_size=embargo_size,
    )
    # skfolio's iterator wants a pandas-shaped X. Just use the returns array.
    X = pd.DataFrame({"r": rets})

    sharpes: list[float] = []
    is_ranks: list[int] = []
    oos_ranks: list[int] = []
    for train_idx, test_idx in cv.split(X):
        if len(test_idx) < 10:
            continue
        test_rets = rets[test_idx]
        train_rets = rets[train_idx]
        if test_rets.std(ddof=1) == 0 or train_rets.std(ddof=1) == 0:
            continue
        s_test = float(test_rets.mean() / test_rets.std(ddof=1) * np.sqrt(252))
        s_train = float(train_rets.mean() / train_rets.std(ddof=1) * np.sqrt(252))
        sharpes.append(s_test)
        # PBO: rank within the fold pair (we have only 2 ranks per pair: train vs test)
        is_ranks.append(2 if s_train >= s_test else 1)
        oos_ranks.append(2 if s_test > s_train else 1)

    if not sharpes:
        return CPCVResult(
            n_paths=0, sharpe_distribution=[], sharpe_mean=0, sharpe_p25=0,
            sharpe_p50=0, sharpe_p75=0, deflated_sharpe=0, pbo=None,
            overfit_warning=True,
        )

    sharpe_arr = np.asarray(sharpes)
    # `pbo` se queda en None: el cálculo con sólo 2 ranks por fold (train vs
    # test del mismo path) es trivialmente ~0.5 y NO es el PBO de López de
    # Prado. La función `probability_of_overfit` quedará disponible para
    # cuando F-stat-quant implemente CPCV real con N estrategias.
    pbo: float | None = None

    # Use the median test Sharpe as the headline; deflate by n_paths trials.
    headline_sharpe = float(np.median(sharpe_arr))
    full_metrics = compute_metrics(
        equity_curve=full.equity_curve,
        trades=[t.model_dump() for t in full.trades],
        initial_equity=base_spec.initial_equity,
        n_trials=len(sharpes),
        annualization_factor=annualization_factor_for(base_spec.timeframe),
    )

    # PBO from `probability_of_overfit` here is a proxy: we only run the
    # strategy once and rank fold sub-samples (López de Prado §12.4 requires
    # reconstructing N independent paths and ranking strategies between them).
    # Until CPCV is reimplemented properly, gate `overfit_warning` on DSR only.
    overfit = full_metrics.deflated_sharpe < 0.5

    log.info(
        "cpcv.done",
        n_paths=len(sharpes),
        sharpe_mean=round(sharpe_arr.mean(), 4),
        dsr=full_metrics.deflated_sharpe,
        pbo="pending_real_cpcv",
    )
    return CPCVResult(
        n_paths=len(sharpes),
        sharpe_distribution=[round(float(s), 4) for s in sharpe_arr],
        sharpe_mean=round(float(sharpe_arr.mean()), 4),
        sharpe_p25=round(float(np.percentile(sharpe_arr, 25)), 4),
        sharpe_p50=round(headline_sharpe, 4),
        sharpe_p75=round(float(np.percentile(sharpe_arr, 75)), 4),
        deflated_sharpe=full_metrics.deflated_sharpe,
        pbo=pbo,
        overfit_warning=overfit,
    )
