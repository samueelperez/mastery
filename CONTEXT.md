# Domain glossary

Canonical terms used across the codebase. Update when terminology resolves; do not write implementation details here.

## Trade lifecycle terms

### Trade Idea
The agent's proposal of a trade. Output of an agent turn (`pydantic_ai` output_type). Carries direction, entry, stop loss, targets, sizing, rationale, and citations. Materialized into a Setup row when the validator accepts the idea.

### Setup
A persisted Trade Idea tracked through its lifecycle by `SetupRuntime`. Stored in `journal_trades`. Has a status, a source (`agent_proposal`, `manual_log`, `paper`, `live`, `csv_import`), and an event timeline in `setup_events`.

### Setup status
- **Pending** — entry not yet reached. Watching for entry hit or invalidation.
- **Active** — entry hit. Watching for stop loss or target hits.
- **Closed** — exited via stop loss, target(s), or manual close. Terminal.
- **Cancelled** — never entered. Either user-cancelled or auto-invalidated. Terminal.

### Stop Loss
Price level that closes an **Active** position with R-multiple = -1. One per setup (per scenario, when scenarios exist). Field: `TradeIdea.stop_loss` / `journal_trades.stop_loss_px`. Event: `sl_hit`.

> *Historical name:* this was `invalidation` / `invalidation_px` before May 2026. Renamed to free the word for the pre-entry concept below.

### Invalidation Condition
A rule that, while a setup is **Pending**, can auto-cancel it before entry is ever reached. Distinct from Stop Loss in two ways: (a) it operates pre-entry, not in-position; (b) it can reference any candle/indicator/regime data, not just a single price. A setup carries zero or more invalidation conditions; if ANY one triggers, the setup transitions Pending → Cancelled. Field: `TradeIdea.invalidation_conditions`. Event: `invalidated`.

### Cancellation
Moving a setup to status **Cancelled**. Two paths:
- **Manual** — user action via UI. Event: `cancelled`.
- **Auto-invalidated** — an invalidation condition fired. Event: `invalidated`.

Both result in the same terminal status but different events so the journal can attribute the reason.

## Modeling terms

### Scenario
An alternate branch on a Trade Idea (typically 2–3 per idea: primary plus alternates). Each scenario may carry its own stop loss; the primary scenario's stop loss is the one persisted on the Setup row.

## Autonomy terms

Decided 2026-05-11. These describe components introduced in the A→B→C plan and shape future agent behavior across the system.

### Scout
The autonomous mode of the main agent. Not a separate agent — the same `Agent` instance is invoked by `scout_dispatcher` (in addition to the on-demand chat path) when a Scanner Rule matches. System prompt and tools are identical to on-demand use. The dispatcher injects the trigger context into the user message ("Scanner rule R matched on {symbol}@{tf}: {condition}. Evaluate setup feasibility.").

### Scanner Rule
An Alert Rule with `is_scout_trigger=true`. When the DSL evaluator matches, the dispatcher invokes Scout for deep analysis instead of notifying the human directly. Reuses `panel_service` and the existing `dsl.RuleSpec` shape.

### Risk Manager
Deterministic component (no LLM) that manages active setups in real time: move-to-breakeven after configurable R, partial closes at TP1/TP2, trailing stop after TP1 (ATR / chandelier / fixed-pct), max-hold by timeframe. Runs on candle close. State persisted in `setups.risk_state` jsonb. The LLM is deliberately kept out of this path because latency (5–30s per review) and non-determinism are unacceptable in gestión activa.

### Factor Gate
Validator layer that consults `factor_stats_repo` and blocks or degrades a Trade Idea whose `factor_snapshot` references factors with weak historical performance. Progressive by sample size: n<30 advisory, 30≤n<100 forces `confidence='low'`, n≥100 with `WR_LCB` below threshold triggers `ModelRetry`. Distinct from the citation gate — operates on factors, not on numbers.

### WR_LCB
Lower Confidence Bound of the win-rate, computed Bayesian with Jeffreys prior over `factor_outcomes`. Used by the Factor Gate as the discriminator for "this factor is empirically weak in this user's history."

### Slippage Buffer
Per-symbol additive padding applied by the validator to the minimum R:R it accepts. Bootstrap defaults conservative (0.3 for BTC/ETH, 0.5 for mid-caps). Ex-post calibration job recomputes per symbol from `paper_fills.slippage_bps_observed` so the buffer tracks real shortfall.

### Paper Fill
A simulated execution of a setup under the paper trading engine. Records intended vs filled entry/exit, slippage in bps, fees, and funding cost prorated by hold time. Feeds the slippage calibration loop.
