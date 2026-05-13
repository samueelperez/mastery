"""Smoke test de los structlog events que el dispatcher emite (F6).

No tocamos DB ni agent — solo verificamos que el módulo expone los nombres
de events esperados como string constants en cualquier path razonable. Es
un "snapshot test" defensivo: si renombras un event sin actualizar el
panel admin/dashboards que los lee, este test grita.
"""

from __future__ import annotations

from app.reviewer.dispatcher import REVIEW_MODEL_ID
from app.reviewer.system_prompt import REVIEW_SYSTEM_PROMPT_VERSION


def test_review_constants_present() -> None:
    assert REVIEW_MODEL_ID  # exact id no se pinea aquí — puede cambiar a Haiku/Opus
    assert REVIEW_SYSTEM_PROMPT_VERSION


def test_review_event_names_documented() -> None:
    """Sanity: los nombres canónicos de los structlog events siguen vivos
    en el código fuente del dispatcher. Si renombras uno, este test te
    obliga a buscar y actualizar dashboards/queries que los referencian."""
    import inspect

    from app.runtime import review_dispatcher

    source = inspect.getsource(review_dispatcher)
    for evt in [
        "review.dispatched",
        "review.completed",
        "review.failed",
        "review.cooldown_blocked",
        "review.cap_reached",
        "review.publish_failed",
        "review.setup_no_longer_active",
    ]:
        assert evt in source, f"missing event log: {evt}"
