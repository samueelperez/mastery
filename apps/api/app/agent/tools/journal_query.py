"""get_similar_past_trades tool — hybrid search over the journal."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from app.agent.deps import AgentDeps
from app.agent.tools._envelope import Provenance, ToolResult
from app.journal.embeddings import INPUT_TYPE_QUERY, embed_one
from app.journal.repo import hybrid_search
from app.journal.summary import TradeSummaryInput, build_summary_text


class PostMortemSummary(BaseModel):
    """Snippet del post-mortem adjunto a un trade similar. Solo poblado
    cuando el trade tiene un post-mortem persistido (LEFT JOIN en
    hybrid_search). Permite al agente ver QUÉ se aprendió del trade
    análogo además de su outcome numérico."""

    verdict: str
    lesson_es: str
    failure_factors: list[str]
    success_factors: list[str]
    confidence_calibration: str


class SimilarTradeOut(BaseModel):
    trade_id: str
    trade_ts: datetime
    symbol: str
    timeframe: str
    side: str
    setup_tag: str
    regime: str
    r_multiple: float | None
    summary: str
    rrf_score: float
    # F5.5: lección extraída tras cerrar el trade (si existe post-mortem).
    # El agente principal usa esto para detectar patrones recurrentes:
    # "5 de 7 trades análogos tienen verdict=thesis_broken citando
    # ema_stack@1h — exigir confirmación adicional".
    post_mortem: PostMortemSummary | None = None


def register_journal_query_tool(agent: Agent[AgentDeps, object]) -> None:
    @agent.tool
    async def get_similar_past_trades(
        ctx: RunContext[AgentDeps],
        setup_features: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "Free-form features describing the current setup — keys we "
                    "use: setup_tag, regime, symbol, timeframe, side, mistakes, "
                    "free_text. We embed these into a query string."
                )
            ),
        ],
        k: Annotated[int, Field(ge=1, le=20)] = 5,
    ) -> ToolResult[list[SimilarTradeOut]]:
        """Retrieve the top-K historical trades most similar to the current setup.

        Uses Reciprocal Rank Fusion of dense (voyage-4-large embeddings) and
        sparse (Postgres tsvector BM25) ranks. `setup_features` should describe
        the *current* setup so the agent can ground claims like "this setup
        won in 7 of the last 10 similar contexts" — and cite the trade IDs.
        """
        # Build a query string the embedding model can use; reuse the same
        # template as ingestion so corpus and query live in the same space.
        synthetic_trade: TradeSummaryInput = {
            "setup_tag": str(setup_features.get("setup_tag") or "unknown"),
            "regime": str(setup_features.get("regime") or "unknown_regime"),
            "side": str(setup_features.get("side") or ""),
            "symbol": str(setup_features.get("symbol") or ""),
            "timeframe": str(setup_features.get("timeframe") or ""),
            "r_multiple": None,
            "mistakes": (
                str(setup_features.get("mistakes") or setup_features.get("free_text") or "")
                or None
            ),
        }
        query_text = build_summary_text(synthetic_trade)
        query_emb = await embed_one(query_text, input_type=INPUT_TYPE_QUERY)

        async with ctx.deps.session_factory() as session:
            hits = await hybrid_search(
                session,
                user_id=ctx.deps.user_id,
                query_text=query_text,
                query_embedding=query_emb,
                k=k,
            )

        out = [
            SimilarTradeOut(
                trade_id=h.id,
                trade_ts=h.trade_ts,
                symbol=h.symbol,
                timeframe=h.timeframe,
                side=h.side,
                setup_tag=h.setup_tag,
                regime=h.regime,
                r_multiple=h.r_multiple,
                summary=h.summary_text,
                rrf_score=round(h.rrf_score, 4),
                post_mortem=(
                    PostMortemSummary(
                        verdict=h.post_mortem.verdict,
                        lesson_es=h.post_mortem.lesson_es,
                        failure_factors=h.post_mortem.failure_factors,
                        success_factors=h.post_mortem.success_factors,
                        confidence_calibration=h.post_mortem.confidence_calibration,
                    )
                    if h.post_mortem is not None
                    else None
                ),
            )
            for h in hits
        ]
        ctx.deps.log.info(
            "tool.get_similar_past_trades",
            n_hits=len(out),
            k=k,
            features=list(setup_features.keys()),
        )
        # `as_of` is the most recent matched trade or now if no hits.
        as_of = max((h.trade_ts for h in hits), default=datetime.fromtimestamp(0, tz=UTC))
        return ToolResult(
            data=out,
            provenance=Provenance(
                source="db.journal_trades:hybrid_search",
                as_of=as_of if out else datetime.fromtimestamp(0, tz=UTC),
                rows=len(out),
                warnings=[] if out else ["no historical trades match — journal too small or no overlap"],
            ),
        )
