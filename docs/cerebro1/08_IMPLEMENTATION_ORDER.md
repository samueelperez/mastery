# 08 — Implementation Order

<context>
This is the day-by-day plan to ship Cerebro 1 in one week of dedicated Claude Code time. Each day produces one mergeable PR. The order respects dependencies: nothing references code that doesn't exist yet. If you must deviate, deviate in the direction of doing LESS, not more — partial implementation is fine; out-of-order is not.

Total scope: ~2,500 lines of new code + ~500 lines of tests. Realistic for 7 working days.
</context>

<assumptions>
- The `market_trades` table already exists with columns `(ts, symbol, price, size, side)` and is being populated by `LiveIngestion`. If NOT, insert Day 0 below to add trade ingestion first. The market module doc only mentions klines — verify on Day 1 before starting Day 2.
- Migrations 001-024 are clean. Run `alembic upgrade head` once before starting to confirm.
- `agent/tools/_envelope.py::Provenance` and `ToolResult` exist as described in the agent module doc.
- The operator has at least one real Hyperliquid test address with active positions (used in `03_PROVIDER_B_HYPERLIQUID.md` integration tests).
</assumptions>

<day_1_skeleton_and_migration>
**Goal**: foundation in place. No external calls yet.

**Tasks**:
1. Create directory structure:
   ```
   apps/api/app/liquidation/
   ├── __init__.py
   ├── models.py
   ├── repo.py            # empty class stub
   ├── service.py         # empty class stub
   └── providers/
       ├── __init__.py
       └── base.py
   ```
2. Implement `models.py` in full (per spec 01).
3. Implement `providers/base.py` (the ABC).
4. Write Alembic migration `025_liquidation_engine.py` (per spec 01).
5. Write `apps/api/tests/liquidation/test_models.py` (per spec 01).
6. Run `alembic upgrade head`; verify 4 new tables.
7. Run `pytest tests/liquidation/test_models.py`; verify green.

**PR**: `feat(liquidation): models, schema, ABC skeleton`

**Acceptance**: migration up/down clean, models tests pass, imports clean.

**Estimated effort**: 4 hours.
</day_1_skeleton_and_migration>

<day_2_provider_a_derived>
**Goal**: derived provider working end-to-end.

**Pre-check**: open `apps/api/app/market/ohlcv/ingestion_live.py`. Does it persist trades to `market_trades`? If NO, this becomes a 2-PR day: first PR adds trade capture, then the provider PR.

**Tasks**:
1. Create `providers/_leverage.py` (constants + 2 helpers).
2. Create `providers/derived.py`.
3. Create `core/exchanges/hyperliquid_symbols.py` (used later by B; do it now so it's done).
4. Write tests `tests/liquidation/providers/test_leverage.py` and `tests/liquidation/providers/test_derived.py` (per spec 07).
5. Smoke test against real DB: pick a symbol with recent trades, call `DerivedLiquidationProvider.get_heatmap()`, eyeball results.

**PR**: `feat(liquidation): provider A — derived from WS trades`

**Acceptance**: tests green; smoke shows at least 3-5 buckets within ±5% on BTC at 4h timeframe.

**Gotcha**: the `no_lookahead` test for `_compute_buckets` is the most critical. Don't ship without it.

**Estimated effort**: 6 hours (most of the day, including the smoke test debugging).
</day_2_provider_a_derived>

<day_3_provider_b_hyperliquid>
**Goal**: Hyperliquid provider operational; address universe bootstrapping.

**Tasks**:
1. Create `providers/_hyperliquid_client.py`.
2. Create `providers/_hyperliquid_bootstrap.py`.
3. Create `providers/hyperliquid.py`.
4. Add the bootstrap task to `core/runtime.py` (or wherever lifespan startups are registered — usually `main.py::lifespan`). The bootstrap should run alongside `LiveIngestion`.
5. Write unit tests `tests/liquidation/providers/test_hyperliquid.py` (mocked).
6. Write integration tests `tests/liquidation/providers/test_hyperliquid_integration.py`.
7. Run the app locally for 30 minutes; verify `hyperliquid_known_addresses` table gets populated with ≥100 addresses.

**PR**: `feat(liquidation): provider B — Hyperliquid on-chain + address bootstrap`

**Acceptance**:
- `to_hyperliquid("BTCUSDT") == "BTC"`.
- Integration test against real API passes (`-m integration`).
- After 30 min of bootstrap, ≥100 addresses in DB.
- Mocked unit tests cover empty universe, errors, multi-coin filtering.

**Gotcha**: the WS message format from Hyperliquid is undocumented for the `trades` channel. Confirm shape with a quick manual `wscat`-like test before relying on `data.get("channel") == "trades"`.

**Estimated effort**: full day (8 hours).
</day_3_provider_b_hyperliquid>

<day_4_service_and_calibration>
**Goal**: aggregation working with uniform weights; calibration job ready (idle until M2).

**Tasks**:
1. Implement `repo.py` in full (per spec 04).
2. Implement `service.py` (per spec 04).
3. Implement `calibration.py` (per spec 04).
4. Write tests `tests/liquidation/test_repo.py`, `test_service.py`, `test_calibration.py`.
5. Add module to lifespan: build `HeatmapService` instance and store on app state so tools can grab it.

**PR**: `feat(liquidation): service aggregator + calibration job`

**Acceptance**:
- `HeatmapService.get_snapshot("BTCUSDT", "4h", current_price)` returns a `HeatmapSnapshot` with both providers contributing.
- `sources_agreement` is non-trivial (≠ 1.0 from a single provider).
- Calibration returns empty list with the empty agreement log (no rows yet).
- Repo tests pass with transactional rollback.

**Gotcha**: `_agreement` requires careful handling when one provider returns empty buckets — should return 0.5 (neutral), not crash on `statistics.mean([])`.

**Estimated effort**: 6 hours.
</day_4_service_and_calibration>

<day_5_agent_tool_and_validator>
**Goal**: agent can call the tool; validator enforces citation contract.

**Tasks**:
1. Create `tool.py` (per spec 05).
2. Modify `agent/agent.py::build_agent()` to call `register_liquidation_tool(agent)` alphabetically.
3. Modify `reviewer/agent.py::build_review_agent()` similarly.
4. Modify `agent/system_prompt.py::TOOLS_CATALOG` to include the new entry alphabetically (preserving byte-stability).
5. Modify `agent/validators.py::must_cite_quantitative_claims` to add the 4 checks from spec 05.
6. Add 4 tests to `tests/agent/test_validators_citation_rigor.py`.
7. Update `setups/repo.py::insert_setup_from_idea` (or equivalent) to persist the heatmap citation snapshot under `factor_snapshot.get_liquidation_heatmap` key.

**PR**: `feat(liquidation): agent tool + citation contract`

**Acceptance**:
- Manual REPL: build agent, ask it for a setup on BTC, tool gets invoked, snapshot returned.
- All 4 new citation tests pass.
- System prompt cache key didn't change (byte-equal except the new alphabetical insertion).

**Gotcha**: the `nearest_long_liq_price` / `nearest_short_liq_price` fields don't exist on `HeatmapSnapshot` directly — they're computed from `nearest_long_liq.price_low` etc. Make sure the snapshot dict the LLM cites is structured to include both (or document the exact shape it must use).

**Estimated effort**: 6 hours.
</day_5_agent_tool_and_validator>

<day_6_telegram_integration>
**Goal**: operator can validate setups via Telegram.

**Tasks**:
1. Create `liquidation/telegram_handlers.py` (per spec 06).
2. Modify `notifications/telegram.py::format_setup_alert` to append the magnet zone section conditionally.
3. Modify `notifications/telegram.py::_inline_kb_for_setup` to add the `gt:*` buttons.
4. Modify `notifications/routes.py::_telegram_webhook` to dispatch `gt:*` callbacks.
5. Add `ground_truth_collection_enabled` flag to `core/config.py::Settings`.
6. Write tests `tests/liquidation/test_telegram_handlers.py` and extend `tests/notifications/test_telegram_format.py`.
7. Manual test: end-to-end. Trigger a setup; verify the magnet zone preview appears on the phone; tap a `gt:*` button; verify row in `liquidation_agreement_log`.

**PR**: `feat(liquidation): Telegram ground-truth collection`

**Acceptance**:
- Setup alert renders magnet zone section in MarkdownV2 without escape errors.
- 3 `gt:*` buttons appear on real phone.
- Tapping a button creates an `liquidation_agreement_log` row with correct deltas.
- `answer_callback_query` ack returns within 200ms.

**Gotcha**: MarkdownV2 escaping is a minefield. Test with edge-case prices (e.g. `$84,500.50`, negative distances) before declaring done. The `$` is escaped in MarkdownV2.

**Estimated effort**: full day (8 hours including manual phone testing).
</day_6_telegram_integration>

<day_7_smoke_dashboards_docs>
**Goal**: production-grade observability and documentation.

**Tasks**:
1. Add Prometheus metrics in the service (counter `liq_snapshots_total`, histogram `liq_snapshot_latency_seconds`, gauge `liq_active_addresses`, counter `liq_provider_errors_total`).
2. Create Grafana panel JSON `infra/grafana/dashboards/liquidation-overview.json` with 4 panels: snapshots/hr by provider; agreement rate rolling 7d; latency p95 per provider; active addresses count.
3. Create `docs/architecture/liquidation.md` — single module doc following the pattern of the other 12 docs. Sections: overview, file layout, public API, internal design, runtime tasks, persistence, observability, gotchas, tests.
4. Update root `CHANGELOG.md` with the M1 release notes for this module.
5. Update `README.md` if applicable (mention new tool).
6. Run full test suite end to end: `pytest tests/ -q`. Must be green.
7. Run app locally for 1 hour; tail logs; confirm no exceptions, address universe growth, snapshots accumulating in DB.

**PR**: `feat(liquidation): observability + docs (M1 complete)`

**Acceptance**:
- Grafana panel renders with real data.
- `docs/architecture/liquidation.md` exists and reads as a peer to the existing 12 docs.
- Full test suite green.
- App runs 1 hour locally without warnings/errors.

**Estimated effort**: 5 hours.
</day_7_smoke_dashboards_docs>

<dependency_graph>
```
Day 1 ─┐
       ├──> Day 2 (Provider A) ─┐
       └──> Day 3 (Provider B) ─┴──> Day 4 (Service) ──> Day 5 (Tool) ──> Day 6 (Telegram) ──> Day 7 (Obs/Docs)
```

Days 2 and 3 are parallelizable — but you're a single agent, so do them sequentially in this order. Day 2 first because it has fewer external dependencies (no HTTP client, no leaderboard scraping).
</dependency_graph>

<global_acceptance_checklist>

When you reach end-of-Day-7, verify ALL of:

- [ ] All 9 specs in `docs/specs/liquidation/` are read and addressed.
- [ ] Migration 025 applied cleanly.
- [ ] 4 new tables exist with correct schemas and indexes.
- [ ] `apps/api/app/liquidation/` exists with the file layout from `CLAUDE.md::file_layout_rules`.
- [ ] Two providers (`A_derived`, `B_hyperliquid`) operational for BTC, ETH, SOL at 1h, 4h, 1d.
- [ ] `HeatmapService.get_snapshot()` returns a valid snapshot from both providers.
- [ ] Tool `get_liquidation_heatmap` registered alphabetically in `agent/agent.py` and `reviewer/agent.py`.
- [ ] Citation contract validator extended with 4 checks; tests green.
- [ ] Telegram setup alert includes magnet zone preview when citation exists.
- [ ] Telegram inline keyboard has 3 `gt:*` buttons; webhook persists verdicts.
- [ ] `liquidation_agreement_log` populated by each Telegram tap.
- [ ] Grafana panel exists and shows live data.
- [ ] `docs/architecture/liquidation.md` exists and matches the format of other 12 module docs.
- [ ] CHANGELOG updated.
- [ ] `pytest tests/ -q` is fully green.
- [ ] 1 hour of local runtime without crashes.

If ANY checkbox is unchecked, the module is not done. Don't move on to Cerebro 2.
</global_acceptance_checklist>

<m2_followup>
NOT for this work. Listed here so you don't include them by mistake.

The following items are deferred to M2 (after weeks 1-4 of paper trading + agreement data collection):

- Coinglass provider activation (decision based on agreement vs TD).
- Adaptive weights via `compute_provider_weights` run weekly.
- Disable `ground_truth_collection_enabled` flag (set to False).
- Cerebro 2: Mean-Reversion Engine.
- Cerebro 3+: remaining 5 brains.

If during M1 implementation you find yourself wanting to add Coinglass support or weight tuning — DON'T. Park it as a TODO comment with `# M2:` prefix.
</m2_followup>

<deviation_protocol>

If you must deviate from this plan:

1. State the deviation explicitly in your PR description.
2. Reference the spec section that becomes invalid as a result.
3. Update the spec in the same PR.
4. Get explicit operator approval before merging if the deviation crosses a `<critical_invariants>` boundary from `CLAUDE.md`.

Common acceptable deviations:
- Add a helper function not listed in the spec — fine, mention it in the PR.
- Skip an optional warning string — fine if the underlying behavior is preserved.
- Refactor an existing module's internal function to share with this one — fine, but bundle the refactor in a separate prep PR.

Common unacceptable deviations:
- Adding a new dependency to `pyproject.toml` — needs explicit approval.
- Changing the citation contract structure — would break existing tools.
- Renaming any public model or field — breaks downstream agents.
- Persisting snapshots as aggregated rows instead of raw buckets — loses the ability to re-aggregate with new weights retroactively.
</deviation_protocol>
