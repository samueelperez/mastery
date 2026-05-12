"""get_similar_past_setups tool — typed similarity search + aggregate stats.

Complementary to ``get_similar_past_trades`` (which takes a free-form dict
of features). This tool has a typed signature explicitly designed for the
moment the agent is about to emit a TradeIdea — it captures the WHY of the
setup (``bias`` + ``confluences_summary``) in addition to the WHAT (symbol,
timeframe, regime). The query text composed from those fields is richer
than ``setup_tag | regime | symbol`` alone, so the embedding-side recall
of relevant historical setups is meaningfully higher.

Beyond returning the top-K hits, it surfaces a small aggregate of the
cluster: win_rate, mean_r, thesis_break_rate. These three numbers are what
the agent actually needs to decide "this kind of setup historically pays —
proceed" vs "this kind of setup historically fails — reconsider".

Internals:
- Builds a query string from the typed fields.
- Embeds via voyage-4-large (input_type='query'), same model as the
  document side so the spaces match.
- Calls ``journal_repo.hybrid_search`` (RRF over dense + BM25).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from app.agent.deps import AgentDeps
from app.agent.tools._envelope import Provenance, ToolResult
from app.journal.embeddings import INPUT_TYPE_QUERY, embed_one
from app.storage.journal_repo import JournalSearchHit, hybrid_search

Bias = Literal["bull", "bear", "range"]


class SimilarSetupHit(BaseModel):
    """One historical trade returned by the similarity search."""

    trade_id: str
    trade_ts: datetime
    symbol: str
    timeframe: str
    side: str
    regime: str
    r_multiple: float | None
    similarity_score: float  # RRF score; higher = more similar
    summary: str
    # If the trade has a post-mortem persisted, the verdict drives whether
    # the cluster suggests "this pattern works" or "this pattern breaks".
    pm_verdict: str | None  # thesis_held | thesis_broken | execution_error | noise
    pm_lesson_es: str | None


class SimilarSetupAggregate(BaseModel):
    """Aggregate read of the top-K cluster — the actionable summary."""

    n_hits: int
    n_with_outcome: int  # hits where r_multiple is not None (i.e. closed)
    win_rate: float | None  # fraction of closed trades with r_multiple > 0.2
    mean_r: float | None  # average r_multiple across closed trades
    thesis_break_rate: float | None  # fraction of hits with pm_verdict='thesis_broken'
    n_thesis_broken: int
    n_thesis_held: int


class SimilarSetupsOut(BaseModel):
    """Bundle: hits + aggregate. The agent should reason from `aggregate`
    before drilling into individual hits."""

    setups: list[SimilarSetupHit]
    aggregate: SimilarSetupAggregate
    interpretation: str


# -----------------------------------------------------------------------------
# Pure helpers — easily unit-testable
# -----------------------------------------------------------------------------


def build_query_text(
    *,
    symbol: str,
    timeframe: str,
    bias: str,
    confluences_summary: str,
    regime: str,
) -> str:
    """Compose the query text that gets embedded.

    Same shape as ``app.journal.summary.build_summary_text`` so the corpus
    and query live in the same space — but front-loads ``regime`` and
    ``bias`` (the most discriminative fields per F2 research) and tacks the
    free-text rationale at the end where BM25 can still see it.
    """
    parts = [
        f"setup_proposal | {regime or 'unknown_regime'} | "
        f"{symbol or ''} {timeframe or ''} {bias or ''}".strip(),
    ]
    if confluences_summary and confluences_summary.strip():
        parts.append(f"rationale: {confluences_summary.strip()}")
    return " | ".join(parts)


def bias_to_side(bias: str) -> str:
    """Map a market-bias label to the side string the journal stores
    (``long``/``short``). Returns ``""`` for ``"range"`` or unknowns —
    side-agnostic queries are still meaningful."""
    if bias == "bull":
        return "long"
    if bias == "bear":
        return "short"
    return ""


def aggregate_hits(hits: list[JournalSearchHit]) -> SimilarSetupAggregate:
    """Compute win_rate / mean_r / thesis_break_rate from a list of hits.

    Pure function — testable without DB or embeddings."""
    n_hits = len(hits)
    closed = [h for h in hits if h.r_multiple is not None]
    n_with_outcome = len(closed)
    if n_with_outcome > 0:
        wins = sum(1 for h in closed if (h.r_multiple or 0.0) > 0.2)
        mean_r: float | None = sum(h.r_multiple or 0.0 for h in closed) / n_with_outcome
        win_rate: float | None = wins / n_with_outcome
    else:
        mean_r = None
        win_rate = None

    n_broken = sum(1 for h in hits if h.post_mortem and h.post_mortem.verdict == "thesis_broken")
    n_held = sum(1 for h in hits if h.post_mortem and h.post_mortem.verdict == "thesis_held")
    pm_total = sum(1 for h in hits if h.post_mortem is not None)
    thesis_break_rate: float | None = n_broken / pm_total if pm_total > 0 else None

    return SimilarSetupAggregate(
        n_hits=n_hits,
        n_with_outcome=n_with_outcome,
        win_rate=win_rate,
        mean_r=mean_r,
        thesis_break_rate=thesis_break_rate,
        n_thesis_broken=n_broken,
        n_thesis_held=n_held,
    )


def build_interpretation(agg: SimilarSetupAggregate) -> str:
    """Short text helping the agent connect the aggregate to a decision."""
    if agg.n_hits == 0:
        return (
            "Sin trades históricos comparables. La tesis NO está respaldada "
            "ni contradicha por tu historial — opera con cautela y trata "
            "este setup como reconocimiento, no confirmación."
        )

    parts: list[str] = []
    if agg.win_rate is not None and agg.mean_r is not None:
        parts.append(
            f"De {agg.n_with_outcome} cerrados similares, WR={agg.win_rate:.0%} "
            f"con R medio {agg.mean_r:+.2f}"
        )
    else:
        parts.append(f"{agg.n_hits} similares pero ninguno cerrado todavía — no hay outcome")

    if agg.thesis_break_rate is not None:
        parts.append(
            f"thesis_broken {agg.thesis_break_rate:.0%} "
            f"({agg.n_thesis_broken}/{agg.n_thesis_broken + agg.n_thesis_held})"
        )

    decision_hint: str
    if agg.win_rate is not None and agg.win_rate < 0.4:
        decision_hint = ". Cluster con WR bajo — exige confluencia adicional o considera no_trade."
    elif agg.thesis_break_rate is not None and agg.thesis_break_rate > 0.6:
        decision_hint = (
            ". Mayoría thesis_broken — los post-mortems sugieren que algo "
            "estructural rompe estos setups; revisa qué factor común falló."
        )
    elif agg.win_rate is not None and agg.win_rate >= 0.6 and (agg.mean_r or 0) > 0:
        decision_hint = (
            ". Cluster sólido — el patrón funciona en tu historial. Mantén "
            "tu disciplina habitual, no infles sizing."
        )
    else:
        decision_hint = ""

    return ". ".join(parts) + decision_hint


# -----------------------------------------------------------------------------
# Tool registration
# -----------------------------------------------------------------------------


def register_similar_setups_tool(agent: Agent[AgentDeps, object]) -> None:
    @agent.tool
    async def get_similar_past_setups(
        ctx: RunContext[AgentDeps],
        symbol: str,
        timeframe: Literal["15m", "1h", "4h", "1d"],
        bias: Bias,
        regime: str,
        confluences_summary: Annotated[
            str,
            Field(
                description=(
                    "Texto breve (≤200 chars) que captura POR QUÉ este setup "
                    "es atractivo. Ej: 'EMA21>55 con RSI 38 en rebote desde "
                    "POC + funding -0.05% extremo'. Lo más específico, "
                    "mejor — esto va al embedding del query."
                )
            ),
        ],
        top_k: Annotated[int, Field(ge=1, le=10)] = 5,
    ) -> ToolResult[SimilarSetupsOut]:
        """Recupera los top-K trades históricos del usuario más similares al
        SETUP que está construyendo el agente, no a un trade ya cerrado.

        Diferencia con `get_similar_past_trades`: esta tool toma una signatura
        TIPADA (symbol/timeframe/bias/regime/confluences_summary) — útil
        cuando el agente está a punto de emitir un TradeIdea y necesita
        validar contra historial. Adicionalmente devuelve un AGREGADO del
        cluster (win_rate, mean_r, thesis_break_rate) que NO está en la
        otra tool y es lo que realmente decide.

        Cuándo invocarla: ANTES de finalizar una TradeIdea con confidence
        >= 'medium'. Si el agregado dice WR < 40% o thesis_break_rate > 60%,
        reconsidera — esa señal vence al gut feel.

        Internamente: build query text → embed con voyage-4-large
        (input_type='query') → hybrid search (RRF dense + BM25). El query
        text incluye `confluences_summary` que es lo que NINGÚN tool
        anterior captura.
        """
        query_text = build_query_text(
            symbol=symbol,
            timeframe=timeframe,
            bias=bias,
            confluences_summary=confluences_summary,
            regime=regime,
        )
        # Voyage embed is the only external API in the hot path; degrade
        # gracefully if it fails (missing key, 5xx after retries, quota).
        # The SQL still runs BM25-only when query_embedding=[] — the hybrid
        # CTE just contributes 0 dense rows, recall drops but the tool
        # doesn't crash the chat turn.
        warnings: list[str] = []
        query_emb: list[float] = []
        try:
            query_emb = await embed_one(query_text, input_type=INPUT_TYPE_QUERY)
        except Exception as exc:
            ctx.deps.log.warning(
                "tool.get_similar_past_setups.embed_failed",
                error=f"{type(exc).__name__}: {str(exc)[:120]}",
            )
            warnings.append(
                f"embed_unavailable: similarity restricted to BM25 ({type(exc).__name__})"
            )

        async with ctx.deps.session_factory() as session:
            hits = await hybrid_search(
                session,
                user_id=ctx.deps.user_id,
                query_text=query_text,
                query_embedding=query_emb,
                k=top_k,
            )

        # Optional: enforce same-side bias filtering. We DON'T at the SQL
        # level because RRF + the LLM's own judgement of relevance is more
        # forgiving — sometimes a contrarian trade IS informative. But we
        # downrank obviously irrelevant sides by surfacing them last when
        # the agent reads the response. (Implementation choice: leave raw
        # for now; agent has all the metadata.)

        agg = aggregate_hits(hits)
        interpretation = build_interpretation(agg)

        setups = [
            SimilarSetupHit(
                trade_id=h.id,
                trade_ts=h.trade_ts,
                symbol=h.symbol,
                timeframe=h.timeframe,
                side=h.side,
                regime=h.regime,
                r_multiple=h.r_multiple,
                similarity_score=round(h.rrf_score, 4),
                summary=h.summary_text,
                pm_verdict=h.post_mortem.verdict if h.post_mortem else None,
                pm_lesson_es=h.post_mortem.lesson_es if h.post_mortem else None,
            )
            for h in hits
        ]

        ctx.deps.log.info(
            "tool.get_similar_past_setups",
            symbol=symbol,
            timeframe=timeframe,
            bias=bias,
            top_k=top_k,
            n_hits=len(setups),
            win_rate=agg.win_rate,
            thesis_break_rate=agg.thesis_break_rate,
        )

        as_of = max((h.trade_ts for h in hits), default=datetime.now(tz=UTC))
        if not setups:
            warnings.append(
                "no historical setups match — journal sparse or no overlap"
            )
        return ToolResult(
            data=SimilarSetupsOut(
                setups=setups,
                aggregate=agg,
                interpretation=interpretation,
            ),
            provenance=Provenance(
                source="db.journal_trades:similar_setups_search",
                as_of=as_of,
                rows=len(setups),
                warnings=warnings,
            ),
        )
