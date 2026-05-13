"""Tests para el holdout split determinista (EXT-4).

`compute_is_holdout(trade_id, user_id, holdout_pct)` debe ser:
1. Determinista — mismo input siempre → mismo output.
2. Sin data leakage temporal — no depende de when/timestamps.
3. Estadísticamente cerca del holdout_pct objetivo sobre suficientes muestras.
"""

from __future__ import annotations

import uuid

from app.setups.repo import DEFAULT_HOLDOUT_PCT, compute_is_holdout


class TestComputeIsHoldout:
    def test_deterministic_same_input_same_output(self) -> None:
        tid = str(uuid.uuid4())
        uid = "user-abc"
        runs = [compute_is_holdout(trade_id=tid, user_id=uid) for _ in range(10)]
        assert all(r == runs[0] for r in runs), "compute_is_holdout no es determinista"

    def test_different_users_different_buckets(self) -> None:
        """Mismo trade_id con distinto user_id puede caer en distinto bucket
        — el split scope-es por usuario (cada user tiene su propio holdout)."""
        tid = "fixed-trade-id"
        results = {compute_is_holdout(trade_id=tid, user_id=f"u{i}") for i in range(50)}
        assert len(results) == 2, "Se esperan ambos buckets sobre 50 usuarios distintos"

    def test_holdout_pct_zero_means_no_holdout(self) -> None:
        for i in range(20):
            assert compute_is_holdout(
                trade_id=str(uuid.uuid4()), user_id="u", holdout_pct=0
            ) is False

    def test_holdout_pct_hundred_means_all_holdout(self) -> None:
        for i in range(20):
            assert compute_is_holdout(
                trade_id=str(uuid.uuid4()), user_id="u", holdout_pct=100
            ) is True

    def test_distribution_approx_matches_pct(self) -> None:
        """Sobre 1000 trades sintéticos, el % holdout debe estar dentro de
        ±5pp del target (15%). Binomial std ≈ 1.1pp con n=1000, así que
        ±5pp es conservador."""
        n = 1000
        holdout_count = sum(
            compute_is_holdout(
                trade_id=str(uuid.uuid4()),
                user_id="bench-user",
                holdout_pct=DEFAULT_HOLDOUT_PCT,
            )
            for _ in range(n)
        )
        pct = holdout_count / n * 100
        assert abs(pct - DEFAULT_HOLDOUT_PCT) < 5.0, (
            f"holdout pct={pct:.1f}% lejos del target {DEFAULT_HOLDOUT_PCT}%"
        )

    def test_distribution_balanced_per_pct(self) -> None:
        """Verifica que pcts intermedios también se distribuyen bien."""
        for target_pct in (5, 25, 50, 75):
            n = 800
            holdout_count = sum(
                compute_is_holdout(
                    trade_id=str(uuid.uuid4()),
                    user_id="bench",
                    holdout_pct=target_pct,
                )
                for _ in range(n)
            )
            pct = holdout_count / n * 100
            assert abs(pct - target_pct) < 6.0, (
                f"target={target_pct}% observed={pct:.1f}%"
            )
