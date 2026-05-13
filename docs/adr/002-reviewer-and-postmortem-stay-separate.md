# ADR-002 — Reviewer and post-mortem agents stay as two distinct agents

- **Status**: Accepted (2026-05-13)
- **Sprint**: M1-polish
- **Supersedes**: `PLAN_MAESTRO.md` §4 phrase
  "Reviewer y post_mortem … están fusionados en un único Supervisor"

## Context

`PLAN_MAESTRO.md` §4 declares the reviewer (post-entry, pre-close) and
post-mortem (post-close) responsibilities will be fused into a single
"Supervisor" agent with one system prompt and two invocation modes. The
2026-05-13 audit of `apps/api/app/reviewer/` and `apps/api/app/post_mortem/`
shows two **independent** pydantic-AI agents in production:

- different system prompts (review = trade-still-valid? vs post-mortem =
  what factors held / failed?)
- different `output_type` models (`TradeReview` vs `PostMortem`)
- different `thinking` levels (review = `"low"`, post-mortem = `"medium"`)
- different tool subsets (post-mortem excludes journal-write tools)
- different cost telemetry (`setup_reviews.cost_usd` vs
  `setup_post_mortems.cost_usd`)

Merging the two into a single agent would require either a longer
prompt with branching ("if mode=review do X, if mode=post_mortem do Y")
or a meta-controller picking which prompt to inject per call. The first
fights Anthropic prompt caching (one big prompt that varies less is
better than a longer prompt that varies more); the second adds a
deciding point the operator does not need.

## Decision

Keep two independent pydantic-AI agents (`reviewer/agent.py` and
`post_mortem/agent.py`) with their own system prompts, output types,
thinking levels, and persistence tables. They will continue to share
the dispatcher pattern (`*/dispatcher.py`) and the cost helper
(`app/agent/cost.py`, PR-11).

## Consequences

- **Pro**: prompt-cache effectiveness is maximised — each agent's
  byte-stable prefix is shorter and never branches.
- **Pro**: cost tuning is independent per agent (current
  `REVIEW_PRICE_*` settings already split this).
- **Pro**: failure of one agent type (e.g. post-mortem prompt
  regression) cannot crash the other.
- **Con**: maintenance cost of two prompt files; in practice these
  diverge intentionally so this is feature not bug.
- **Con**: `PLAN_MAESTRO.md` §4 needs the update applied in PR-12.
