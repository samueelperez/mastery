"""Pure-function tests for `app.post_mortem.dispatcher` helpers.

La auditoría 2026-05 marcó como Important #1 que el `dispatcher.py` no tenía
tests. Este archivo cubre las funciones puras `_outcome_from_r` y
`_exit_reason_from_trigger` que son las más fáciles de regresarse en
refactors silenciosos.

Las funciones I/O (`_compute_mfe_mae`, `_build_factor_verdicts`) requieren
fixtures de DB y se cubrirán con tests de integración cuando se añadan.
"""

from __future__ import annotations

import pytest

from app.post_mortem.dispatcher import _exit_reason_from_trigger, _outcome_from_r


# -----------------------------------------------------------------------------
# _outcome_from_r
# -----------------------------------------------------------------------------


class TestOutcomeFromR:
    def test_none_returns_loss(self) -> None:
        assert _outcome_from_r(None, "setup_closed_sl") == "loss"
        assert _outcome_from_r(None, "setup_closed_tp") == "loss"

    @pytest.mark.parametrize(
        "r,trigger,expected",
        [
            # SL closes: r ≤ 0 → loss; r > 0 → breakeven (post-BE move)
            (-1.0, "setup_closed_sl", "loss"),
            (-0.5, "setup_closed_sl", "loss"),
            (0.0, "setup_closed_sl", "loss"),
            (0.1, "setup_closed_sl", "breakeven"),
            (1.5, "setup_closed_sl", "breakeven"),
            # TP closes: r > 0.2 → win; else → breakeven
            (0.0, "setup_closed_tp", "breakeven"),
            (0.1, "setup_closed_tp", "breakeven"),
            (0.2, "setup_closed_tp", "breakeven"),  # exclusivo > 0.2
            (0.21, "setup_closed_tp", "win"),
            (3.5, "setup_closed_tp", "win"),
        ],
    )
    def test_outcome_thresholds(self, r: float, trigger: str, expected: str) -> None:
        assert _outcome_from_r(r, trigger) == expected


# -----------------------------------------------------------------------------
# _exit_reason_from_trigger
# -----------------------------------------------------------------------------


class TestExitReasonFromTrigger:
    @pytest.mark.parametrize(
        "trigger,reason",
        [
            ("setup_closed_sl", "sl_hit"),
            ("setup_closed_tp", "tp_hit"),
            ("manual_close", "manual_close"),
            ("price_move", "manual_close"),  # fallback para otros triggers
            ("anything_else", "manual_close"),
        ],
    )
    def test_exit_reason_mapping(self, trigger: str, reason: str) -> None:
        assert _exit_reason_from_trigger(trigger) == reason
