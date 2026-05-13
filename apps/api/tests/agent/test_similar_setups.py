"""A.7 — Pure tests for similar_setups tool helpers.

The async tool wraps voyage embeddings + a hybrid_search DB call, both E2E.
This file pins the policy logic the tool keys off:

- `build_query_text` — composes the embedding query from typed setup fields.
- `bias_to_side` — bias→side mapping.
- `aggregate_hits` — win_rate / mean_r / thesis_break_rate from hits.
- `build_interpretation` — surfaces the right decision hint by bucket.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.agent.tools.similar_setups import (
    SimilarSetupAggregate,
    aggregate_hits,
    bias_to_side,
    build_interpretation,
    build_query_text,
)
from app.journal.repo import JournalSearchHit, PostMortemHitInfo

# ----------------------------------------------------------------------------
# build_query_text
# ----------------------------------------------------------------------------


def test_query_text_includes_all_typed_fields() -> None:
    q = build_query_text(
        symbol="BTCUSDT",
        timeframe="1h",
        bias="bull",
        confluences_summary="EMA21>55 con RSI 38",
        regime="trending_up",
    )
    assert "BTCUSDT" in q
    assert "1h" in q
    assert "bull" in q
    assert "trending_up" in q
    assert "EMA21>55" in q


def test_query_text_handles_empty_confluences() -> None:
    q = build_query_text(
        symbol="ETHUSDT",
        timeframe="4h",
        bias="bear",
        confluences_summary="",
        regime="trending_down",
    )
    assert "ETHUSDT" in q
    assert "trending_down" in q
    # No rationale block when confluences_summary is empty
    assert "rationale:" not in q


def test_query_text_strips_whitespace_in_rationale() -> None:
    q = build_query_text(
        symbol="SOLUSDT",
        timeframe="15m",
        bias="bull",
        confluences_summary="   funding extremo + OI cargando   ",
        regime="ranging",
    )
    assert "rationale: funding extremo + OI cargando" in q


# ----------------------------------------------------------------------------
# bias_to_side
# ----------------------------------------------------------------------------


def test_bias_to_side_bull() -> None:
    assert bias_to_side("bull") == "long"


def test_bias_to_side_bear() -> None:
    assert bias_to_side("bear") == "short"


def test_bias_to_side_range_returns_empty() -> None:
    assert bias_to_side("range") == ""


def test_bias_to_side_unknown_returns_empty() -> None:
    assert bias_to_side("weird") == ""


# ----------------------------------------------------------------------------
# aggregate_hits
# ----------------------------------------------------------------------------


def _hit(
    *,
    trade_id: str = "abc",
    r: float | None = None,
    pm_verdict: str | None = None,
) -> JournalSearchHit:
    pm = (
        PostMortemHitInfo(
            verdict=pm_verdict,
            lesson_es="x",
            failure_factors=[],
            success_factors=[],
            confidence_calibration="calibrated",
        )
        if pm_verdict
        else None
    )
    return JournalSearchHit(
        id=trade_id,
        trade_ts=datetime.now(tz=UTC),
        symbol="BTCUSDT",
        timeframe="1h",
        side="long",
        setup_tag="test",
        regime="trending_up",
        r_multiple=r,
        summary_text="x",
        rrf_score=0.5,
        post_mortem=pm,
    )


def test_aggregate_empty_hits() -> None:
    agg = aggregate_hits([])
    assert agg.n_hits == 0
    assert agg.win_rate is None
    assert agg.mean_r is None
    assert agg.thesis_break_rate is None


def test_aggregate_all_closed_majority_wins() -> None:
    hits = [_hit(r=1.5), _hit(r=2.0), _hit(r=-1.0), _hit(r=0.8)]
    agg = aggregate_hits(hits)
    assert agg.n_hits == 4
    assert agg.n_with_outcome == 4
    # 3 of 4 with r > 0.2 → wins
    assert agg.win_rate == pytest.approx(0.75)
    assert agg.mean_r == pytest.approx((1.5 + 2.0 - 1.0 + 0.8) / 4)


def test_aggregate_skips_unclosed_for_win_rate() -> None:
    hits = [_hit(r=2.0), _hit(r=None), _hit(r=-1.0)]
    agg = aggregate_hits(hits)
    assert agg.n_hits == 3
    assert agg.n_with_outcome == 2  # only 2 have r_multiple
    assert agg.win_rate == pytest.approx(0.5)


def test_aggregate_breakeven_does_not_count_as_win() -> None:
    hits = [_hit(r=0.1), _hit(r=0.15)]  # both below 0.2 threshold
    agg = aggregate_hits(hits)
    assert agg.win_rate == 0.0


def test_aggregate_thesis_break_rate() -> None:
    hits = [
        _hit(r=1.0, pm_verdict="thesis_held"),
        _hit(r=-1.0, pm_verdict="thesis_broken"),
        _hit(r=-1.0, pm_verdict="thesis_broken"),
        _hit(r=0.5),  # no post-mortem
    ]
    agg = aggregate_hits(hits)
    assert agg.n_thesis_broken == 2
    assert agg.n_thesis_held == 1
    # break_rate = 2 broken out of (2 + 1) = 3 with post-mortem
    assert agg.thesis_break_rate == pytest.approx(2 / 3)


def test_aggregate_no_post_mortems_returns_none_break_rate() -> None:
    hits = [_hit(r=1.0), _hit(r=-0.5)]
    agg = aggregate_hits(hits)
    assert agg.thesis_break_rate is None
    assert agg.n_thesis_broken == 0
    assert agg.n_thesis_held == 0


# ----------------------------------------------------------------------------
# build_interpretation
# ----------------------------------------------------------------------------


def _agg(
    *,
    n_hits: int = 5,
    n_with_outcome: int = 5,
    win_rate: float | None = 0.5,
    mean_r: float | None = 0.2,
    thesis_break_rate: float | None = None,
    n_thesis_broken: int = 0,
    n_thesis_held: int = 0,
) -> SimilarSetupAggregate:
    return SimilarSetupAggregate(
        n_hits=n_hits,
        n_with_outcome=n_with_outcome,
        win_rate=win_rate,
        mean_r=mean_r,
        thesis_break_rate=thesis_break_rate,
        n_thesis_broken=n_thesis_broken,
        n_thesis_held=n_thesis_held,
    )


def test_interpretation_zero_hits_warns_no_history() -> None:
    msg = build_interpretation(_agg(n_hits=0, n_with_outcome=0, win_rate=None, mean_r=None))
    assert "Sin trades" in msg or "sin trades" in msg.lower()


def test_interpretation_low_win_rate_suggests_caution() -> None:
    msg = build_interpretation(_agg(n_hits=10, n_with_outcome=10, win_rate=0.3, mean_r=-0.2))
    assert "no_trade" in msg.lower() or "exige" in msg.lower()


def test_interpretation_high_thesis_break_rate_suggests_review() -> None:
    msg = build_interpretation(
        _agg(
            n_hits=8,
            n_with_outcome=8,
            win_rate=0.5,
            mean_r=0.1,
            thesis_break_rate=0.75,
            n_thesis_broken=6,
            n_thesis_held=2,
        )
    )
    assert "thesis_broken" in msg.lower() or "estructural" in msg.lower()


def test_interpretation_solid_cluster_acknowledges_pattern_works() -> None:
    msg = build_interpretation(_agg(n_hits=6, n_with_outcome=6, win_rate=0.66, mean_r=0.8))
    assert "funciona" in msg.lower() or "sólido" in msg.lower() or "solido" in msg.lower()


def test_interpretation_open_only_says_no_outcome() -> None:
    msg = build_interpretation(_agg(n_hits=3, n_with_outcome=0, win_rate=None, mean_r=None))
    assert "no hay outcome" in msg.lower() or "cerrado todav" in msg.lower()
