"""Tests pure-function de la lógica del review scheduler (sin DB).

`compute_next_review_at` decide cuándo el time-scheduler dispara el próximo
`time_elapsed` review. Reglas:
- Devuelve el siguiente offset > now (en horas desde entry_hit_at).
- Si entry_hit_at es None → None (setup pending, no aplica).
- Si todos los offsets ya pasaron → None (terminamos los reviews por tiempo;
  los otros triggers siguen activos).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.reviewer.repo import compute_next_review_at


def test_no_entry_hit_returns_none() -> None:
    now = datetime(2026, 5, 11, 12, 0, tzinfo=UTC)
    assert compute_next_review_at(
        entry_hit_at=None, now=now, offsets_hours=(4, 24, 72)
    ) is None


def test_first_offset_when_just_after_entry() -> None:
    entry = datetime(2026, 5, 11, 10, 0, tzinfo=UTC)
    now = entry + timedelta(minutes=30)  # 30 min después del entry
    nxt = compute_next_review_at(
        entry_hit_at=entry, now=now, offsets_hours=(4, 24, 72)
    )
    assert nxt == entry + timedelta(hours=4)


def test_second_offset_when_past_first() -> None:
    entry = datetime(2026, 5, 11, 10, 0, tzinfo=UTC)
    now = entry + timedelta(hours=5)
    nxt = compute_next_review_at(
        entry_hit_at=entry, now=now, offsets_hours=(4, 24, 72)
    )
    assert nxt == entry + timedelta(hours=24)


def test_returns_none_when_all_offsets_past() -> None:
    entry = datetime(2026, 5, 11, 10, 0, tzinfo=UTC)
    now = entry + timedelta(hours=100)  # mucho después del último offset (72)
    assert compute_next_review_at(
        entry_hit_at=entry, now=now, offsets_hours=(4, 24, 72)
    ) is None


def test_offsets_sorted_internally() -> None:
    """Si nos pasan offsets desordenados, igual seleccionamos el siguiente."""
    entry = datetime(2026, 5, 11, 10, 0, tzinfo=UTC)
    now = entry + timedelta(hours=5)
    nxt = compute_next_review_at(
        entry_hit_at=entry, now=now, offsets_hours=(72, 4, 24)
    )
    assert nxt == entry + timedelta(hours=24)
