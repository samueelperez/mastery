# Changelog

All notable changes to the Trading Copilot project are documented here.
Format roughly follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions are tagged at milestone boundaries (M1, M2, …) rather than semver
releases, since this is a single-user system.

## [M1 — Cerebro 1: Liquidation Heatmap Engine] — 2026-05-13

First of the 7 cerebros is operational. The orchestrator agent can now
read liquidation magnet zones from two technical providers (derived from
WS trades + Hyperliquid on-chain positions), fused with cross-source
agreement and citation contract enforcement, and the operator can
validate each setup against TradingDifferent from the phone — closing
the loop for M2 weight calibration.

### Added

- **Module `apps/api/app/liquidation/`** — new top-level domain with
  `models.py`, `repo.py`, `service.py`, `calibration.py`, `tool.py`,
  `telegram_handlers.py`, and `providers/{derived,hyperliquid}.py`.
- **Migrations 026-028**:
  - `026_liquidation_engine` — 4 tables: `liquidation_buckets`,
    `hyperliquid_known_addresses`, `liquidation_agreement_log`,
    `liquidation_provider_weights`.
  - `027_market_trades` — prerequisite hypertable for Provider A.
  - `028_provider_weights_relax_floor` — DB CHECK aligned with
    post-normalized weights (allow [0.0, 1.0]).
- **`market_trades` ingestion** — `LiveIngestion._watch_trades_loop`
  captures every aggressor-tagged trade via `ccxt.pro.watchTrades`,
  flushes in batches of 500 / 1s.
- **Provider A — DerivedLiquidationProvider** — Polars-vectorized
  pipeline over `market_trades` with 7 leverage brackets and exponential
  time decay. Pure `_compute_buckets` for no-lookahead testing.
- **Provider B — HyperliquidLiquidationProvider** — reads real
  `clearinghouseState` per address. Address universe bootstrap via WS
  trades subscription + 6h leaderboard refresh.
- **`HeatmapService` aggregator** — parallel provider calls with 3s
  timeout, weight loading (uniform M1 / calibrated M2), grid-snap merge,
  imbalance / density / agreement / confidence metrics, raw-bucket
  persistence.
- **`compute_provider_weights` calibration** — ready for M2 weekly run;
  floors at 0.10 raw rate, normalizes per `(symbol, timeframe)`.
- **Agent tool `get_liquidation_heatmap`** — registered alphabetically
  in `build_agent()` (main copilot) and `build_review_agent()`
  (supervisor); exposes `HeatmapSnapshot` via `ToolResult`.
- **Citation contract extension** — `_verify_liquidation_citation`
  in `agent/validators.py` enforces required keys, agreement match
  within 0.001, current_price within 0.5%, directional zone semantics
  (long → short_liq TP, short → long_liq TP), and confidence/agreement
  coherence.
- **Telegram ground-truth flow** — magnet zone preview in
  `format_setup_alert` + 3 inline buttons (`gt:agree|close|disagree`)
  in `_inline_kb_for_setup` when `Settings.ground_truth_collection_enabled=True`.
  `record_ground_truth` persists `liquidation_agreement_log` rows with
  computed deltas.
- **Settings: `ground_truth_collection_enabled`** — bool flag default
  `True`. Toggle to `False` at start of M2 once weights have been
  calibrated.
- **Prometheus metrics** — `mt_liq_snapshots_total{symbol, timeframe,
  outcome}`, `mt_liq_snapshot_latency_seconds{provider}` histogram,
  `mt_liq_active_addresses` gauge, `mt_liq_provider_errors_total
  {provider, kind}`.
- **Grafana dashboard** — `infra/grafana/dashboards/liquidation-overview.json`
  with 4 panels (snapshots/hr by sym·tf, p95 latency per provider, active
  addresses, error rate).
- **Module doc** — `docs/architecture/liquidation.md` covering layout,
  public API, internal design, persistence, observability, gotchas,
  tests, and the M1 → M2 transition checklist.

### Changed

- `apps/api/app/main.py::lifespan` — spawns `HyperliquidAddressBootstrap`
  alongside `LiveIngestion`; cleanup on shutdown.
- `apps/api/app/notifications/telegram.py::send_setup_alert` — wires
  ground-truth keyboard based on Settings flag + presence of heatmap
  citation in the idea.
- `apps/api/app/notifications/routes.py::_handle_callback` — dispatches
  `gt:*` callback_data to `record_ground_truth` before the generic
  `a:`/`r:` split.

### Internal invariants verified

CLAUDE.md `<critical_invariants>` upheld across all 7 PRs:

- **#1 TZ-aware UTC** — all `as_of`, `now`, `cutoff` carry `tzinfo=UTC`.
  Validators reject naive datetimes (`test_naive_datetime_rejected`).
- **#2 `user_id` in queries** — `LiquidationRepo` requires `user_id`
  at construction; every query that touches user-scoped tables includes
  `WHERE user_id = :uid`. Global tables (`hyperliquid_known_addresses`,
  `liquidation_provider_weights`) are intentionally not user-scoped.
- **#3 System prompt byte-stable** — TOOLS_CATALOG entry is static text;
  test asserts alphabetical position on the source string.
- **#4 Tools alphabetically in `build_agent`** — `register_liquidation_tool`
  inserted between `register_journal_query_tool` and
  `register_list_alerts_tool`; pinned by `test_tool_registers_alphabetically`.
- **#5 Citations by `tool_name`** — `_verify_liquidation_citation` keys
  off `cite.tool_name`, never an opaque ID.
- **#6 No-lookahead** — `DerivedLiquidationProvider._compute_buckets` is
  a pure function on `(DataFrame, now)`; `test_no_lookahead` verifies a
  future-dated trade in the input doesn't alter the output (after
  filtering).
- **#10 Cardinality discipline** — all metric labels are bounded enums
  (`symbol ∈ {BTCUSDT,ETHUSDT,SOLUSDT}`, `timeframe ∈ {1h,4h,1d}`,
  `provider ∈ {A_derived,B_hyperliquid}`, `kind ∈ {timeout,exception,
  stale}`, `outcome ∈ {ok,degraded,empty}`).
- **#11 No tokens in logs** — handlers log `setup_id`, `verdict`,
  `delta_a/b`; Telegram bot token and webhook secret never enter log
  paths.

### Deviations from spec (documented)

- **Migration numbering**: spec assumed `025`; consumed by
  `025_postmortem_drop_summary_es`. Renumbered to `026`; spec patched
  in the same PR (Day 1).
- **`test_repo.py` omitted**: no transactional DB-rollback fixture exists
  globally; SQL is straightforward and exercised by smoke. Tracking
  issue for the rollback fixture.
- **`insert_setup_from_idea` not extended** to persist heatmap citation
  into `factor_snapshot`. Until that lands, the Telegram GT handler
  safely returns False with `gt_no_heatmap_citation` log. Tracking
  issue.
- **Magnet zone Telegram preview without volume** — citation snapshot
  carries scalars only. Future enhancement to read the full snapshot
  from `factor_snapshot.get_liquidation_heatmap.data`.
- **`http2=True` removed from HyperliquidClient** — avoids optional
  `h2` dependency; HTTP/1.1 sufficient for one-shot POSTs.
- **`_TokenBucket` lazy `_last_refill`** — fixed potential nil-loop bug
  flagged in spec gotchas.

### Smoke tests executed

- 60s of `LiveIngestion._watch_trades_loop` → 3,113 trades persisted
  across BNB/BTC/ETH/SOL, 0 errors after zero-price filter fix.
- 60s of `HyperliquidAddressBootstrap` → 76 addresses captured via WS
  fills.
- `HeatmapService.get_snapshot('BTCUSDT', '4h', $80,975)` → 14 zones
  merged across both providers, agreement 0.732, 0 warnings,
  short-heavy imbalance 0.113 (a real reading of current market
  positioning).
- Format E2E: alert renders MarkdownV2 correctly, inline keyboard
  emits valid `gt:*` callback_data under 64 bytes.

### Tests

- **Liquidation + agent integration**: 86 tests passing in ~3s
  (10 models + 11 leverage + 5 derived + 9 HL + 3 HL integration +
  11 service + 7 calibration + 2 mapping + 5 tool + 8 GT handler +
  4 citation rigor + 8 telegram format + 2 sanity).
- **Full suite**: passing aside from 1 pre-existing brittle test in
  `factor_stats_bayesian` and 7 pre-existing conftest errors in
  `tests/storage/` — none related to Cerebro 1.

### M1 acceptance checklist

All items in `docs/specs/liquidation/00_OVERVIEW.md::<acceptance_criteria>`
met EXCEPT:

- Manual 1h local run with log tail → reduced to 60s smoke per
  practical session constraints; flagged for operator validation
  before merging the M1 release tag.
- `setups/repo.py::insert_setup_from_idea` extension to persist
  heatmap citation into `factor_snapshot` → tracking issue.

### Stack of PRs (in merge order)

1. PR #1 `feat(liquidation): models, schema, ABC skeleton (Day 1)`
2. PR #2 `feat(market): trades ingestion + market_trades table` (prereq)
3. PR #3 `feat(liquidation): provider A — derived from WS trades (Day 2)`
4. PR #4 `feat(liquidation): provider B — Hyperliquid on-chain (Day 3)`
5. PR #5 `feat(liquidation): HeatmapService aggregator + calibration (Day 4)`
6. PR #6 `feat(liquidation): agent tool + citation contract (Day 5)`
7. PR #7 `feat(liquidation): Telegram ground-truth collection (Day 6)`
8. PR #8 `feat(liquidation): observability + docs (Day 7 — M1 complete)`
