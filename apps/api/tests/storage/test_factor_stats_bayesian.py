"""Pure-unit tests para las fórmulas Bayesian de factor_stats_repo.

No tocan DB — testan exclusivamente `_posterior_stats` (Beta-Binomial
posterior con prior `Beta(α₀=2, β₀=2)`).
"""

from __future__ import annotations

import pytest
from scipy.stats import beta as beta_dist

from app.storage.factor_stats_repo import (
    PRIOR_ALPHA,
    PRIOR_BETA,
    _posterior_stats,
)


class TestPosteriorStats:
    def test_lcb_le_mean_le_ucb_invariant(self) -> None:
        """Para cualquier (wins, n), lcb <= mean <= ucb siempre."""
        for wins, n in [(0, 0), (0, 10), (5, 10), (10, 10), (3, 5), (2, 100)]:
            mean, lcb, ucb = _posterior_stats(wins=wins, n=n)
            assert 0.0 <= lcb <= mean <= ucb <= 1.0, (
                f"invariant violated for wins={wins} n={n}: "
                f"lcb={lcb} mean={mean} ucb={ucb}"
            )

    def test_zero_observations_returns_prior(self) -> None:
        """Con n=0 el posterior == prior Beta(2,2). Mean = 0.5."""
        mean, lcb, ucb = _posterior_stats(wins=0, n=0)
        assert mean == pytest.approx(0.5, abs=1e-9)
        # Beta(2,2): 5th percentile ≈ 0.0976, 95th ≈ 0.9024
        expected_lcb = float(beta_dist.ppf(0.05, PRIOR_ALPHA, PRIOR_BETA))
        expected_ucb = float(beta_dist.ppf(0.95, PRIOR_ALPHA, PRIOR_BETA))
        assert lcb == pytest.approx(expected_lcb, abs=1e-6)
        assert ucb == pytest.approx(expected_ucb, abs=1e-6)

    def test_small_sample_lcb_low_even_if_mean_high(self) -> None:
        """n=3, wins=2 (raw 67%) DEBE reportar lcb mucho menor que la
        media — el "trust floor" baja por incertidumbre."""
        mean, lcb, ucb = _posterior_stats(wins=2, n=3)
        # Posterior Beta(2+2, 2+1) = Beta(4, 3). Mean = 4/7 ≈ 0.571.
        assert mean == pytest.approx(4.0 / 7.0, abs=1e-6)
        # Penalización clave: el lcb cae lejos del 67% raw.
        assert lcb < 0.30, f"lcb={lcb} debería ser <0.30 con n=3"
        assert ucb > 0.85, f"ucb={ucb} debería ser >0.85 con n=3"

    def test_large_sample_narrows_interval(self) -> None:
        """Con muchas observaciones consistentes, el intervalo se estrecha
        alrededor del verdadero ratio."""
        # n=100, wins=60 → tasa verdadera ≈ 60%.
        _mean, lcb_100, ucb_100 = _posterior_stats(wins=60, n=100)
        # Con n=10, wins=6: misma proporción pero menor confianza.
        _mean_10, lcb_10, ucb_10 = _posterior_stats(wins=6, n=10)
        width_100 = ucb_100 - lcb_100
        width_10 = ucb_10 - lcb_10
        assert width_100 < width_10, (
            f"n=100 width={width_100} no es menor que n=10 width={width_10}"
        )
        # Mean ≈ (60+2)/(100+4) ≈ 0.596
        assert lcb_100 > 0.50, "n=100 wins=60 lcb debería superar 50%"

    def test_all_wins_does_not_imply_100pct(self) -> None:
        """n=5, wins=5 NO reporta 100% — la incertidumbre del prior persiste."""
        mean, lcb, ucb = _posterior_stats(wins=5, n=5)
        # Posterior Beta(7, 2). Mean = 7/9 ≈ 0.778.
        assert mean < 0.95
        assert lcb < 0.55, f"lcb={lcb} debería penalizar n pequeño"

    def test_all_losses_does_not_imply_0pct(self) -> None:
        mean, lcb, ucb = _posterior_stats(wins=0, n=5)
        # Posterior Beta(2, 7). Mean = 2/9 ≈ 0.222.
        assert mean > 0.05
        assert ucb > 0.40, f"ucb={ucb} debería reflejar incertidumbre"

    def test_invalid_inputs_raise(self) -> None:
        with pytest.raises(ValueError):
            _posterior_stats(wins=-1, n=10)
        with pytest.raises(ValueError):
            _posterior_stats(wins=5, n=-1)
        with pytest.raises(ValueError):
            _posterior_stats(wins=11, n=10)  # wins > n
