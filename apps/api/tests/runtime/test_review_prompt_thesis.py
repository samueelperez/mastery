"""Test que el `_build_review_user_prompt` inyecte la TESIS ORIGINAL del
setup (regime, confidence, summary_es, confluences, scenarios) cuando los
campos están presentes — y que tolere gracefully setups pre-migration 010
con todos los campos None.

Esto es crítico: si futuros refactors descartan campos del prompt, el
review_agent se queda sin la tesis y debe re-derivarla vía tools.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.reviewer.dispatcher import _build_review_user_prompt
from app.setups.repo import OpenSetupRow


def _setup_with_thesis(**overrides: object) -> OpenSetupRow:
    base = {
        "id": "00000000-0000-0000-0000-000000000001",
        "user_id": "u1",
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "side": "long",
        "status": "active",
        "entry_px": 64000.0,
        "stop_loss_px": 63500.0,
        "targets": [{"label": "TP1", "price": 65000.0, "rationale": "x"}],
        "invalidation_conditions": [],
        "expires_at": None,
        "proposed_at": datetime(2026, 5, 11, 10, 0, tzinfo=UTC),
        "entry_hit_at": datetime(2026, 5, 11, 11, 0, tzinfo=UTC),
        "regime": "trending_up",
        "confidence": "high",
        "summary_es_full": (
            "Entra largo en pullback al EMA21. Estructura HH-HL "
            "intacta en 4h. Riesgo: ruptura de 63.5k."
        ),
        "confluences": [
            {"timeframe": "4h", "bias": "bull", "narrative": "EMA stack alineado"},
            {"timeframe": "1h", "bias": "bull", "narrative": "Pullback respeta EMA21"},
        ],
        "scenarios": [
            {
                "label": "A",
                "probability_pct": 60,
                "description": "Pullback al EMA21 → long",
                "entry": 64000.0,
                "stop_loss": 63500.0,
                "target": 65000.0,
            },
        ],
    }
    base.update(overrides)
    return OpenSetupRow(**base)  # type: ignore[arg-type]


def test_prompt_includes_full_thesis() -> None:
    setup = _setup_with_thesis()
    prompt = _build_review_user_prompt(
        setup=setup,
        trigger_kind="time_elapsed",
        trigger_payload={"hours_since_entry": 4.0},
        current_price=64500.0,
        candle_ts=setup.entry_hit_at + timedelta(hours=4),  # type: ignore[operator]
        prior_reviews=[],
    )
    assert "Tesis original" in prompt
    assert "trending_up" in prompt
    assert "confidence=high" in prompt
    assert "EMA21" in prompt  # del summary_es_full
    assert "[4h]" in prompt  # del confluences
    assert "EMA stack alineado" in prompt
    assert "A (60%)" in prompt  # scenarios


def test_prompt_omits_thesis_block_when_all_empty() -> None:
    """Setups pre-migration 010 tienen todos los campos None/[]. El bloque
    debe omitirse silenciosamente — no contaminamos el prompt con
    'Tesis original: (sin datos)'."""
    setup = _setup_with_thesis(
        regime=None,
        confidence=None,
        summary_es_full=None,
        confluences=[],
        scenarios=[],
    )
    prompt = _build_review_user_prompt(
        setup=setup,
        trigger_kind="entry_hit",
        trigger_payload={"entry_px": 64000.0},
        current_price=64000.0,
        candle_ts=setup.entry_hit_at,  # type: ignore[arg-type]
        prior_reviews=[],
    )
    assert "Tesis original" not in prompt


def test_prompt_includes_prior_reviews_when_present() -> None:
    setup = _setup_with_thesis()
    prior = [
        {
            "created_at": "2026-05-11T11:30:00Z",
            "trigger_kind": "entry_hit",
            "current_state": "on_track",
            "recommendation": "hold",
            "summary": "Trade recién activado, estructura intacta.",
        }
    ]
    prompt = _build_review_user_prompt(
        setup=setup,
        trigger_kind="time_elapsed",
        trigger_payload={"hours_since_entry": 4.0},
        current_price=64500.0,
        candle_ts=setup.entry_hit_at + timedelta(hours=4),  # type: ignore[operator]
        prior_reviews=prior,
    )
    assert "Reviews previas" in prompt
    assert "on_track/hold" in prompt
    assert "Trade recién activado" in prompt
