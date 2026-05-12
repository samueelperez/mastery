"""A.3 — Recurring lessons preamble: pure tests for fingerprint + clustering.

The async `get_recurring_lessons_for_preamble` query is exercised indirectly
via the chat.py preamble; here we pin the heuristics that decide WHICH
lessons collapse into a cluster and how the top-K ordering works. No DB.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.storage.post_mortem_repo import (
    _cluster_lessons,
    _lesson_fingerprint,
)

# ---------------------------------------------------------------------------
# _lesson_fingerprint
# ---------------------------------------------------------------------------


def test_fingerprint_strips_accents_and_case() -> None:
    a = _lesson_fingerprint("Régimen ranging — ema_stack por sí solo no basta")
    b = _lesson_fingerprint("regimen RANGING - ema_stack por si solo no basta")
    assert a == b


def test_fingerprint_drops_short_and_stopwords() -> None:
    # "por", "no" (length<4) and "solo" (stopword) should be dropped.
    fp = _lesson_fingerprint("ema_stack por sí solo no basta")
    # 'ema_stack' and 'basta' should remain
    assert "ema_stack" in fp
    assert "basta" in fp
    assert "solo" not in fp


def test_fingerprint_order_invariant() -> None:
    """Two lessons with the same keyword set in different order produce the
    same fingerprint (we sort)."""
    a = _lesson_fingerprint("volumen confirmar tendencia exigir adicional")
    b = _lesson_fingerprint("exigir tendencia adicional confirmar volumen")
    assert a == b


def test_fingerprint_empty_or_noise_is_empty() -> None:
    assert _lesson_fingerprint("") == ""
    # only short tokens & stopwords → empty fingerprint
    assert _lesson_fingerprint("y el la o un") == ""


def test_fingerprint_distinct_topics_differ() -> None:
    a = _lesson_fingerprint("En régimen ranging, ema_stack@1h por sí solo no basta")
    b = _lesson_fingerprint("Funding rate extremo precede a un squeeze inminente")
    assert a != b


# ---------------------------------------------------------------------------
# _cluster_lessons
# ---------------------------------------------------------------------------


def _row(
    lesson: str,
    *,
    symbol: str = "BTCUSDT",
    ts: datetime | None = None,
) -> dict:
    return {
        "lesson_es": lesson,
        "symbol": symbol,
        "created_at": ts or datetime.now(tz=UTC),
    }


def test_cluster_collapses_near_duplicates() -> None:
    base_time = datetime(2026, 5, 1, tzinfo=UTC)
    rows = [
        _row(
            "En régimen ranging ema_stack solo no basta — exigir volume confirmation",
            symbol="BTCUSDT",
            ts=base_time,
        ),
        _row(
            "Regimen ranging: ema_stack solo no basta, exigir volume confirmation",
            symbol="SOLUSDT",
            ts=base_time + timedelta(hours=2),
        ),
    ]
    clusters = _cluster_lessons(rows, top_k=5, min_occurrences=2)
    assert len(clusters) == 1
    assert clusters[0].n_occurrences == 2
    # Most recent exemplar wins as displayed text
    assert "Regimen ranging" in clusters[0].lesson_es
    assert set(clusters[0].sample_symbols) == {"BTCUSDT", "SOLUSDT"}


def test_cluster_respects_min_occurrences() -> None:
    rows = [
        _row("Lección única A — funding extremo precede squeeze"),
        _row("Lección única B — correlation breakdown con BTC"),
    ]
    # Both singletons → min_occurrences=2 filters them out
    clusters = _cluster_lessons(rows, top_k=5, min_occurrences=2)
    assert clusters == []

    # Lowering min_occurrences to 1 includes them
    clusters_loose = _cluster_lessons(rows, top_k=5, min_occurrences=1)
    assert len(clusters_loose) == 2


def test_cluster_orders_by_count_then_recency() -> None:
    base = datetime(2026, 5, 1, tzinfo=UTC)
    rows = [
        # Cluster A: 3 occurrences, latest at hour 0
        _row("ema_stack solo no basta exigir volume", ts=base),
        _row("ema_stack solo no basta exigir volume", ts=base - timedelta(hours=5)),
        _row("ema_stack solo no basta exigir volume", ts=base - timedelta(hours=10)),
        # Cluster B: 2 occurrences, newer
        _row("funding extremo precede squeeze inminente", ts=base + timedelta(hours=3)),
        _row("funding extremo precede squeeze inminente", ts=base + timedelta(hours=1)),
    ]
    clusters = _cluster_lessons(rows, top_k=5, min_occurrences=2)
    assert len(clusters) == 2
    # Cluster A first because n_occurrences (3) > B's (2)
    assert clusters[0].n_occurrences == 3
    assert "ema_stack" in clusters[0].lesson_es
    assert clusters[1].n_occurrences == 2


def test_cluster_top_k_truncates() -> None:
    # 5 distinct lesson patterns with very different vocab, each x2 occurrences
    patterns = [
        "ema_stack volume confirmation falla regimen ranging",
        "funding extremo precede squeeze long inminente alts",
        "correlation breakdown btc altcoin divergence riesgo",
        "vwap rejection sin estructura previa swing fallido",
        "rsi divergencia bajista contra trend principal",
    ]
    rows = []
    for i, p in enumerate(patterns):
        for j in range(2):
            rows.append(
                _row(
                    p,
                    ts=datetime(2026, 5, 1, i, j, tzinfo=UTC),
                )
            )
    clusters = _cluster_lessons(rows, top_k=3, min_occurrences=2)
    assert len(clusters) == 3


def test_cluster_caps_sample_symbols_at_3() -> None:
    base = datetime(2026, 5, 1, tzinfo=UTC)
    rows = [
        _row("ema_stack solo no basta volumen", symbol=f"SYM{i}USDT", ts=base + timedelta(hours=i))
        for i in range(5)
    ]
    clusters = _cluster_lessons(rows, top_k=5, min_occurrences=2)
    assert len(clusters) == 1
    assert len(clusters[0].sample_symbols) == 3


def test_cluster_skips_blank_lessons() -> None:
    rows = [
        _row("", ts=datetime(2026, 5, 1, tzinfo=UTC)),
        _row("   ", ts=datetime(2026, 5, 1, 1, tzinfo=UTC)),
        _row(
            "real lesson volumen tendencia confirmacion exigir",
            ts=datetime(2026, 5, 1, 2, tzinfo=UTC),
        ),
        _row(
            "real lesson volumen tendencia confirmacion exigir",
            ts=datetime(2026, 5, 1, 3, tzinfo=UTC),
        ),
    ]
    clusters = _cluster_lessons(rows, top_k=5, min_occurrences=2)
    assert len(clusters) == 1
    assert "real lesson" in clusters[0].lesson_es
