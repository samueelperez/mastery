# Liquidation Heatmap Engine (Cerebro 1)

<context>
First of the 7 cerebros powering the autonomous trader. Aggregates liquidation
zones from two technical providers (derived from WS trades + Hyperliquid
on-chain positions) into a single `HeatmapSnapshot` with magnet zones,
density, imbalance, and cross-source agreement.

Exposed to the orchestrator agent as one tool: `get_liquidation_heatmap`.
Calibration of provider weights happens in M2 from ground-truth verdicts
collected via Telegram during weeks 1-4 of paper trading.

Cost: €0 (both providers are free public data). Future provider D_coinglass
is implemented but `enabled=False` by default; activation is one config flag
in M2 if M1 agreement vs TradingDifferent < 80%.
</context>

## File layout

```
apps/api/app/liquidation/
├── __init__.py
├── models.py                          # Pydantic types (HeatmapSnapshot, MagnetZone, …)
├── repo.py                            # LiquidationRepo — DB persistence, scoped by user_id
├── service.py                         # HeatmapService aggregator + metrics
├── calibration.py                     # compute_provider_weights (M2 weekly job)
├── tool.py                            # register_liquidation_tool (pydantic-ai)
├── telegram_handlers.py               # record_ground_truth (Telegram → DB)
└── providers/
    ├── __init__.py
    ├── base.py                        # BaseLiquidationProvider ABC
    ├── _leverage.py                   # LEVERAGE_BRACKETS, MAINTENANCE_MARGIN, helpers
    ├── derived.py                     # Provider A — from market_trades
    ├── _hyperliquid_client.py         # httpx async + rate limiter + tenacity retry
    ├── _hyperliquid_bootstrap.py      # leaderboard + WS trades → address universe
    └── hyperliquid.py                 # Provider B — on-chain positions
```

Mirror layout under `apps/api/tests/liquidation/`.

## Public API

The module exposes exactly ONE entry point to the agent:

```python
# Tool registered alphabetically in build_agent() + build_review_agent().
@agent.tool
async def get_liquidation_heatmap(
    ctx: RunContext[AgentDeps],
    symbol: str,            # 'BTCUSDT' | 'ETHUSDT' | 'SOLUSDT'
    timeframe: Literal["1h", "4h", "1d"] = "4h",
    max_distance_pct: float = 10.0,
) -> ToolResult[HeatmapSnapshot]:
    ...
```

All other types (`HeatmapSnapshot`, `MagnetZone`, `LiquidationRepo`, …) are
internal — used by the service, the tool wrapper, and tests, but never by
external modules.

## Internal design

### Snapshot pipeline (per call)

1. `BinanceAdapter.fetch_ticker(symbol)` → current price.
2. `HeatmapService.get_snapshot(symbol, timeframe, current_price, max_distance_pct)`:
   - **Parallel provider calls** with 3-second timeout via `asyncio.gather`.
     Failures (timeout/exception) flagged in `provenance.warnings`;
     successful but stale results (older than `provider.max_age_seconds`)
     dropped too.
   - **Weight load** from `liquidation_provider_weights` if calibrated,
     else uniform `1/N` per active provider (M1 default).
   - **Merge** to a canonical bucket grid keyed by
     `(floor(price_low/bucket_size)*bucket_size, side)`. Sum
     `est_volume_usd * weight` per bucket; preserve `source_breakdown`.
   - **Derived metrics**:
     - `imbalance_ratio = long_vol / short_vol` within ±5% of current.
     - `cluster_density` = fraction of total volume within ±2% of current.
     - `sources_agreement = 1 - mean(CV(top-5 buckets across providers))`.
     - `nearest_long_liq` / `nearest_short_liq` directional pointers.
   - **Confidence** mapped from agreement: ≥0.85 high, ≥0.60 medium, else low.
   - **Persist raw buckets** to `liquidation_buckets` (best-effort; service
     still returns on persist failure).

### Provider A — DerivedLiquidationProvider

Reconstructs zones from `market_trades` (captured by `LiveIngestion` via
`ccxt.pro.watchTrades` since Day 0 / PR #2).

For each significant trade (≥ $100k notional) in the lookback window:
1. Expand to 7 leverage brackets (3×, 5×, 10×, 25×, 50×, 75×, 100×).
2. Compute counterparty liq price: `trade_px * (1 ± 1/leverage ∓ mm)`.
3. Weight: `notional × leverage_prior_weight × time_decay`
   (`time_decay = exp(-age_hours * ln2 / HALF_LIFE_HOURS)`).
4. Bucket by `floor(liq_px / bucket_size) * bucket_size`; sum weights.

Pipeline is Polars vectorized: a join with the leverage table expands
N×7, the rest is column expressions and a group-by.

`_compute_buckets` is **pure** on `(DataFrame, now)` — this enables the
no-lookahead test (`test_no_lookahead`) per CLAUDE.md invariant #6.

### Provider B — HyperliquidLiquidationProvider

Reads REAL on-chain positions via Hyperliquid's `clearinghouseState`
endpoint. No estimation — `liquidationPx` is exact.

Universe of addresses is maintained by `HyperliquidAddressBootstrap`,
which runs two parallel loops:
- **WS trades** subscribes to public trades; every fill carries
  `users: [buyer, seller]` → upsert.
- **Leaderboard** scraped every 6h as supplementary (the endpoint shape is
  undocumented; defensive fallback to `[]` if it breaks).

Per heatmap call: SELECT top-500 addresses by recency × account value,
`asyncio.gather` clearinghouseState for each, bucket positions whose `coin`
matches the request, sum `positionValue` per bucket.

Rate-limited via token bucket (100 req/min) + semaphore (20 concurrent).
Tenacity retries 429s with exponential jitter.

### Calibration (M2)

`compute_provider_weights(repo)` runs weekly from M2 onward:
1. Pull last 30 days of `liquidation_agreement_log` rows (skipping `skipped`
   verdicts).
2. For each `(symbol, timeframe, provider)` cell with ≥ 10 samples:
   - `agreement_rate = sum(td_agrees AND provider_within_0.5%) / n`
   - `weight = max(WEIGHT_FLOOR=0.10, agreement_rate)`
3. Normalize per `(symbol, timeframe)` so weights sum to 1.0. The stored
   weight can dip below `WEIGHT_FLOOR` after norm (e.g. floor/(floor+1) ≈
   0.091) — that's intentional and the DB CHECK is `weight >= 0.0`
   (migration 028).

## Runtime tasks (lifespan)

Wired in `main.py::lifespan`:

```python
hl_client = HyperliquidClient()                   # owns httpx pool + rate limiter
hl_bootstrap = HyperliquidAddressBootstrap(
    session_factory=db._sessionmaker,
    client=hl_client,
    watch_symbols=settings.watch_symbol_list,
)
await hl_bootstrap.start()
# ... yield ...
await hl_bootstrap.stop()
await hl_client.close()
```

`HeatmapService` is NOT a long-lived runtime task — it's constructed
per-tool-invocation in `liquidation/tool.py` with the providers and a
`LiquidationRepo` bound to the request's user/session.

## Persistence (migrations 026-028)

Four tables introduced by migration **026**:

- `liquidation_buckets(user_id, symbol, timeframe, snapshot_ts, price_low,
  price_high, side, est_volume_usd, source, raw_payload jsonb, created_at)`
  — per-source raw buckets. The aggregated snapshot is NOT persisted;
  reconstructing it from buckets allows retroactive re-aggregation with new
  weights in M2 without re-fetching from providers.
- `hyperliquid_known_addresses(address PK, first_seen_at, last_seen_at,
  last_account_value_usd, n_positions, tags text[])` — Provider B's universe.
  Format CHECK: `address ~ '^0x[a-fA-F0-9]{40}$'`.
- `liquidation_agreement_log(user_id, setup_id, symbol, timeframe,
  proposed_zone_price, proposed_zone_side, source_a_price, source_b_price,
  source_c_verdict, delta_a_pct, delta_b_pct, notes, logged_at)` — drives
  M2 calibration. Populated by Telegram `gt:*` callbacks.
- `liquidation_provider_weights(symbol, timeframe, provider, weight,
  agreement_rate, n_samples, computed_at)` — calibration output. Migration
  **028** relaxes the CHECK from `weight >= 0.10` to `weight >= 0.0`
  (post-norm weights can legitimately dip below floor by construction).

Migration **027** adds `market_trades(id, ts, exchange, symbol, price, size,
side, trade_id)` — prerequisite for Provider A. Timescale hypertable when
the extension is loaded; regular table otherwise.

## Observability

Four Prometheus metrics in `core/observability/metrics.py`:

| Metric | Type | Labels | Purpose |
|--------|------|--------|---------|
| `mt_liq_snapshots_total` | Counter | symbol, timeframe, outcome | Throughput + degraded-rate. Outcome: `ok` (≥2 providers), `degraded` (1 provider), `empty` (none). |
| `mt_liq_snapshot_latency_seconds` | Histogram | provider | Per-call latency. Buckets: 0.05/0.1/0.25/0.5/1/2/3s. Provider timeout is 3s. |
| `mt_liq_active_addresses` | Gauge | (none) | Total rows in `hyperliquid_known_addresses`. Updated after every WS/leaderboard upsert batch. |
| `mt_liq_provider_errors_total` | Counter | provider, kind | Failures. Kind: `timeout`, `exception`, `stale`. |

All labels bounded (symbol/timeframe/provider/kind enumerable) per
CLAUDE.md invariant #10 (no high-cardinality labels).

Grafana dashboard: `infra/grafana/dashboards/liquidation-overview.json`
(4 panels: snapshots/h by symbol·tf, p95 latency per provider, active
addresses gauge, provider error rate).

### Logs

`structlog` JSON. Notable events:
- `heatmap_returned` (zones, agreement, sources, warnings) — every tool call.
- `hl_bootstrap_started`, `hl_leaderboard_synced`, `hl_ws_disconnected`,
  `hl_ws_error`.
- `gt_recorded`, `gt_setup_not_found`, `gt_no_heatmap_citation`.
- `persist_snapshot_failed` (rare; doesn't crash the response).

## Citation contract

`agent/validators.py::_verify_liquidation_citation` enforces (when
`cite.tool_name == "get_liquidation_heatmap"`):

1. Required snapshot keys: `symbol, current_price, sources_agreement,
   sources_used`.
2. The tool was invoked this turn (real tool output exists).
3. Some call returned the cited symbol.
4. `current_price` within 0.5% of real (prices move between call & cite).
5. `sources_agreement` matches real within 0.001 (deterministic).
6. **Directional**: for `target` citations only, long setups citing
   `nearest_long_liq_price` are rejected (and short setups citing
   `nearest_short_liq_price`). Entry/SL/invalidation citations may
   legitimately reference same-side zones — the gate is by label.
7. `confidence='high'` incompatible with `sources_agreement < 0.60`.

## Telegram integration

When `Settings.ground_truth_collection_enabled=True` AND a setup carries a
`get_liquidation_heatmap` citation:

1. `notifications/telegram.py::format_setup_alert` appends:
   ```
   🧲 *Magnet zones (±5%)*
     Below: `82,500` (longs liq)
     Above: `85,400` (shorts liq)
     Agreement: `0.91` (high)

   _Validar contra TradingDifferent:_
   ```
2. `_inline_kb_for_setup(setup_id, with_ground_truth=True)` prepends a row of
   3 buttons: `✅ TD agrees`, `⚠️ TD close`, `❌ TD disagrees`.
3. Webhook dispatches `gt:<verdict>:<setup_id>` to `record_ground_truth`,
   which extracts the heatmap citation from `journal_trades.factor_snapshot`
   and persists a row to `liquidation_agreement_log` with `delta_a_pct` and
   `delta_b_pct` computed from `source_breakdown_*_price`.

Toggle `ground_truth_collection_enabled=False` at the start of M2 once
weights have been calibrated. The same flag re-enables manual ground-truth
collection in M3+ if the system drifts.

## Gotchas

- **`market_trades` is the prerequisite for Provider A**. Captured by
  `LiveIngestion._watch_trades_loop` (PR #2). If absent, Provider A returns
  `no_significant_trades_in_window`.
- **Zero-price trades from ccxt**: rare synthetic emissions with price=0
  break the DB CHECK. Filter in `_watch_trades_loop` before buffering
  (PR #2 fix commit).
- **Hyperliquid leaderboard endpoint shape is undocumented**; if the body
  returns a non-list/non-`leaderboardRows` dict, we log a warning and fall
  back to `[]`. WS path remains the primary universe driver.
- **WEIGHT_FLOOR=0.10** is the floor on the RAW agreement rate inside
  calibration, NOT on the post-normalized stored weight. Migration 028
  relaxes the DB CHECK accordingly.
- **Per-tool BinanceAdapter** in `tool.py` opens an httpx connection per
  invocation. Acceptable for M1 hot-path latency; refactor in M2 to share an
  adapter via `AgentDeps`.
- **`factor_snapshot.get_liquidation_heatmap` not yet populated by
  `insert_setup_from_idea`**: until that builder is extended, the Telegram
  GT handler logs `gt_no_heatmap_citation` and returns False silently. Safe
  but the M2 calibration table won't fill until the persistence path is
  wired.
- **HyperliquidClient HTTP/2 disabled** (no `h2` dep); HTTP/1.1 is fine for
  one-shot POSTs to the info endpoint.
- **`_compute_buckets` is pure**. If you refactor, preserve purity — the
  no-lookahead test depends on it.
- **`sort('weight', descending=True)` is non-stable** with ties. The
  no-lookahead test sorts by `(price_low, side)` before comparing.

## Tests

```
tests/liquidation/
├── conftest.py                                  # shared fixtures
├── test_models.py                               # 10
├── test_service.py                              # 11 (merge, weights, metrics, confidence)
├── test_calibration.py                          # 7 (floor, normalize, skipped, sum=1)
├── test_tool.py                                 # 5 (envelope, alphabetic order, docstring)
├── test_telegram_handlers.py                    # 8 (verdict, missing, deltas, sides)
└── providers/
    ├── test_leverage.py                         # 11 (priors, liq direction, opposite)
    ├── test_derived.py                          # 5 (incl no-lookahead)
    ├── test_hyperliquid.py                      # 9 (mapping + provider mocked)
    └── test_hyperliquid_integration.py          # 3 (real HL API, -m integration)
```

Plus 4 new tests in `tests/agent/test_validators_citation_rigor.py` and
8 new tests in `tests/notifications/test_telegram_format.py`.

Run with:
```bash
pytest tests/liquidation/                         # unit only
pytest tests/liquidation/ -m integration          # include HL API hits
```

## M1 → M2 transition checklist

1. After 4 weeks of paper trading + ≥ 40 ground-truth verdicts collected:
   - Run `compute_provider_weights(repo)` once (manual trigger or
     scheduled task — TBD).
   - Inspect `liquidation_provider_weights` rows; sanity-check the
     `agreement_rate` distribution.
2. Decide on Coinglass activation:
   - If agreement(B_hyperliquid + A_derived) vs TD ≥ 80% → keep
     `D_coinglass.enabled=False`.
   - Else → flip flag, deploy, monitor.
3. Toggle `ground_truth_collection_enabled=False` in Settings.
4. Schedule weekly `compute_provider_weights` job (cron or APScheduler).
5. Document the decision in `docs/adr/` (TBD).

If `agreement_rate` < 0.50 across the board → the providers are
fundamentally miscalibrated; pause and investigate before continuing.
