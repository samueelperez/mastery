# ADR-001 — Tool inventory purge deferred to M2 boundary

- **Status**: Accepted (2026-05-13)
- **Sprint**: M1-polish
- **Supersedes**: implicit assumption in `PLAN_MAESTRO.md` §4 ("Tools en el agente principal: 8")

## Context

`PLAN_MAESTRO.md` §4 declares the main agent should expose exactly 8 tools
(`get_basis`, `get_bias`, `get_indicators`, `get_journal_query`,
`get_liquidation_heatmap`, `get_market_dominance`, `list_alerts`,
`list_open_setups`), reduced from 22 originals — citing prompt-token cost
and attention degradation. The 2026-05-13 audit of
`apps/api/app/agent/agent.py::build_agent()` found **23 tools registered**
in the live code and `list_open_setups` does not exist in the codebase.

Reducing the surface to 8 right now requires (a) deciding which 15 to
drop, (b) verifying no internal flow depends on them, (c) rewriting
prompts that reference them. Doing that without telemetry of actual
per-tool usage is guessing.

## Decision

Keep the current 23-tool inventory through M1 close. Schedule a
data-driven purge at the **M1 → M2 boundary**:

1. Run the agent on at least 30 days of live (or paper) traffic with the
   current inventory.
2. Build a Grafana panel showing per-tool invocation count from
   `mt_agent_invocations_total{kind=chat}` extended with a `tool` label
   (requires a small instrumentation patch — separate PR).
3. Rank tools by usage × marginal value (validator-detected miscites count
   as negative).
4. Cut the bottom quartile in one explicit PR with an eval comparing
   pre/post TradeIdea quality on a held-out scenario set.

The M2 cut may end at 8 tools or somewhere else — that is the right
question for the data to answer, not the plan.

## Consequences

- **Pro**: avoids a guess-driven refactor that could remove a tool the
  agent silently relies on for citation rigor.
- **Pro**: the M2 eval doubles as a regression test for the cut.
- **Con**: system prompt remains larger than the plan assumed; the
  attention-degradation cost is unmeasured until M2. Mitigated by the
  validator's citation gate, which catches the most likely failure mode
  (citing a tool that was not called).

## Implementation notes

- `PLAN_MAESTRO.md` §4 will be updated in PR-12 to reference this ADR
  and call out that the 8-tool target is aspirational, not current.
- `list_open_setups` (referenced in the plan but not in code) is removed
  from the plan's tool list; the working substitute is `get_journal_query`
  filtered on `status IN ('pending','active')`.
