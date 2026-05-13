# ADR-003 — Scout agent migrated to Claude Haiku 4.5

- **Status**: Accepted (2026-05-13)
- **Sprint**: M1-polish
- **Implements**: `PLAN_MAESTRO.md` §4 row "Scout → claude-haiku-4-5"

## Context

`PLAN_MAESTRO.md` §4 assigned `claude-haiku-4-5-20251001` to the scout
role ("binary decisiones rápidas"). The 2026-05-13 audit found that
`apps/api/app/setups/scout_dispatcher.py` instead reuses the main chat
agent (Sonnet 4.6) via ``get_agent()``. The scout fires every time a
``is_scout_trigger=TRUE`` rule matches — far higher frequency than chat,
and a substantial unforced cost item.

## Decision

Introduce a dedicated scout agent factory at
`apps/api/app/setups/scout_agent.py` that wraps `build_agent(model_id=…)`
with `SCOUT_MODEL_ID = "anthropic/claude-haiku-4.5"` (OpenRouter id;
dot, not dash — matches the project's existing model id convention).

Wiring:

- `app/agent/agent.py::build_agent()` now accepts an optional `model_id`
  parameter; the public `get_agent()` continues to default to
  `DEFAULT_MODEL_ID` (Sonnet 4.6) so the main chat is unaffected.
- `app/setups/scout_dispatcher.py::dispatch_scout_match()` switches its
  one call site from `get_agent()` to `get_scout_agent()`. Singleton
  separation keeps both agents' prompt caches isolated — swapping one
  model later does not invalidate the other.
- The full tool catalogue and validators are kept; tool subsetting (no
  journal / backtest tools) is deferred until per-tool usage data is in
  (see [[001-tool-inventory-deferred-to-m2]]).

## Consequences

- **Pro**: ~10× cost reduction per scout invocation. At ~30 scout fires
  / day this is ~€20-30 / month back into infra budget.
- **Pro**: Haiku 4.5 latency is lower; scout's <3s budget is easier to
  hold.
- **Pro**: the parameterised `build_agent` is reusable for the planned
  audit agent (Opus 4.7) when its UI selector lands.
- **Con**: Haiku is "less capable" than Sonnet on long-chain reasoning.
  Scout's narrow task (rule match → propose or skip) does not exercise
  that gap. If a regression in TradeIdea quality is observed, the rollback
  is trivial — set `SCOUT_MODEL_ID = DEFAULT_MODEL_ID`.

## Verification

Tests in `apps/api/tests/setups/test_scout_agent.py` pin the wiring:

- `test_scout_model_id_is_haiku` — constant.
- `test_get_scout_agent_returns_haiku_singleton` — factory returns the
  expected model.
- `test_scout_agent_is_distinct_from_main_chat_agent` — singleton
  separation.
- `test_get_scout_agent_async_returns_same_singleton` — async lock path.
