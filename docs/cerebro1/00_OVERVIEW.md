# Cerebro 1 — Liquidation Heatmap Engine — Overview

<context>
This is the entry point for the `liquidation/` module. Read this first, then jump to the specific spec for the file you are implementing. All 9 docs in this folder together describe a single feature: a new module `apps/api/app/liquidation/` that ingests liquidation data from multiple sources, merges it with adaptive weights, exposes a single pydantic-ai tool `get_liquidation_heatmap`, and integrates with Telegram for human ground-truth calibration.
</context>

<status>
Not implemented. This is greenfield work. No code exists in `apps/api/app/liquidation/` yet. Migrations 001-024 exist; this work introduces migration **025**.
</status>

<scope>
What this module DOES:
- Ingest liquidation-relevant data from 2 technical sources (derived from CCXT WS data + Hyperliquid on-chain).
- Aggregate into a unified `HeatmapSnapshot` with magnet zones, density, imbalance.
- Persist snapshots for historical analysis.
- Expose to the agent via a single tool with strict citation contract.
- Collect ground-truth verdicts from the operator via Telegram inline keyboard (during weeks 1-4 of paper trading).
- Compute adaptive per-source weights from agreement data at the end of week 4.

What this module DOES NOT:
- Predict price.
- Generate trade setups (that's the orchestrator).
- Send alerts in real-time (that's `alerts/`).
- Decide sizing or leverage (that's `setups/` + `backtest/factor_stats`).
- Talk to Coinglass in M1 (deferred to M2 — provider stub exists, activation is a config flag).
</scope>

<sources>
The system uses 2 active providers at launch, 1 deferred:

| Code | Name | Cost | Coverage | Activation |
|------|------|------|----------|------------|
| `A_derived` | Derived from WS trades | €0 | Binance USDM + Bybit USDT perps | Day 1 |
| `B_hyperliquid` | Hyperliquid on-chain | €0 | Hyperliquid DEX | Day 3 |
| `D_coinglass` | Coinglass Hobbyist API | $29/mo | 30+ exchanges aggregated | DEFERRED to M2 — provider class is implemented but `enabled=false` by default. Activated by config flag if M1 agreement vs TradingDifferent < 80%. |

A third "source" (`C_tradingdifferent`) is not a code provider — it's a human-in-the-loop validation step via Telegram. The operator has an active TradingDifferent annual subscription and validates each setup manually during weeks 1-4. Verdicts persist to `liquidation_agreement_log` and drive the adaptive weights.
</sources>

<decisions>
Decisions already made by the strategic partner. Do not re-litigate; implement.

1. **No scraping of TradingDifferent.** Their dashboard is Cloudflare-protected, ToS prohibits programmatic access, and the operator has manual access anyway. We use it as ground truth via Telegram, not as a programmatic source.
2. **Watch list = BTC, ETH, SOL.** Three symbols. Other assets join when system is validated.
3. **Timeframes = 1h, 4h, 1d.** No 15m (too noisy for liquidation analysis), no 1m.
4. **Custom implementation, not `py-liquidation-map` dependency.** That library has 119 stars and last activity in 2023. We borrow the conceptual algorithm (~200 lines) but write our own code so it integrates cleanly with our Polars + asyncio stack and we own the maintenance.
5. **Hyperliquid address bootstrap = leaderboard + WS userEvents.** No external API for "all positions"; we accumulate addresses over time. Reach ~70% of OI coverage in 7 days.
6. **Adaptive weights start uniform.** First 4 weeks: every provider has equal weight 1/N. At end of week 4, `calibration.compute_provider_weights()` runs and replaces uniform weights with agreement-derived weights. Floor at 0.10 so no provider dies permanently.
7. **Coinglass provider is implemented but disabled.** This way M2 activation is one config flag, not a code change.
</decisions>

<glossary>
**Magnet zone**: a price range where leveraged positions cluster their liquidation prices. Acts as a price magnet because triggered liquidations generate forced market orders.

**Cluster density**: a 0-1 scalar describing how concentrated the liquidation volume is in a narrow band (±2% of current price) vs spread over a wider range. High density = stronger short-term magnet effect.

**Imbalance ratio**: long_volume / short_volume within ±5% of current price. >1.5 = long-heavy positioning (potential squeeze down). <0.67 = short-heavy positioning (potential squeeze up).

**Source agreement**: 1 - coefficient of variation across providers for the top 5 buckets by volume. >0.85 = high; 0.60-0.85 = medium; <0.60 = low.

**Provenance**: standard envelope from `agent/tools/_envelope.py` carrying `source`, `as_of`, `rows`, `warnings[]`. Required on every tool output.

**Citation contract**: convention enforced by `agent/validators.py` that every numeric claim in a TradeIdea must trace to a `ToolCitation` with a snapshot dict matching the real tool output within 0.1%.

**Ground truth**: TradingDifferent verdict from the operator. Used to calibrate provider weights. Not a data source for runtime decisions.
</glossary>

<read_order>
For Claude Code starting fresh on this module, read in this order:

1. `00_OVERVIEW.md` (this file)
2. `08_IMPLEMENTATION_ORDER.md` — day-by-day plan, what to build first
3. `01_MODELS_AND_SCHEMA.md` — types and DB before everything else
4. The provider spec you're implementing today (02 or 03)
5. `04_HEATMAP_SERVICE.md` — only after at least one provider works
6. `05_AGENT_TOOL_AND_VALIDATOR.md` — only after service works
7. `06_TELEGRAM_INTEGRATION.md` — last code piece
8. `07_TESTING.md` — referenced throughout, but read end-to-end once

If you are extending an already-implemented module, jump directly to the relevant spec.
</read_order>

<acceptance_criteria>
The module is "done" for M1 when ALL of these are true:

- [ ] Migration 025 applied cleanly on a fresh DB and on a copy of production schema.
- [ ] `apps/api/app/liquidation/` exists with the file structure from `<file_layout_rules>` in CLAUDE.md.
- [ ] Two providers (`A_derived`, `B_hyperliquid`) operational for BTC, ETH, SOL at 1h, 4h, 1d.
- [ ] Tool `get_liquidation_heatmap` registered alphabetically in both `agent/agent.py` and `reviewer/agent.py` (the supervisor agent).
- [ ] Citation contract validator extension passes all tests in `tests/agent/test_validators_citation_rigor.py` (existing) + the 4 new tests added in `07_TESTING.md`.
- [ ] Telegram inline keyboard sends magnet zone preview and accepts ground-truth verdict; verdict persists to `liquidation_agreement_log`.
- [ ] Endpoints `GET /liquidation/heatmap` and `GET /liquidation/calibration` return valid JSON.
- [ ] Grafana panel `liquidation-overview` shows: snapshots per hour by provider, agreement rate rolling 7d, latency p95 per provider, active addresses count (Hyperliquid).
- [ ] All tests under `tests/liquidation/` pass: unit + integration (`-m integration`) + citation contract.
- [ ] CHANGELOG.md updated.
- [ ] Documentation: docstrings complete; `docs/architecture/liquidation.md` created (following the format of the other 12 module docs).
</acceptance_criteria>

<reference_existing_modules>
This module is heavily inspired by existing patterns. Reference these files:

- **Layout**: `apps/api/app/market/` (especially `market/ohlcv/` and `market/indicators/`).
- **Providers pattern**: `apps/api/app/market/dominance/provider.py` (single provider, but same shape).
- **Runtime task pattern**: `apps/api/app/market/ohlcv/ingestion_live.py::LiveIngestion`.
- **Repo pattern**: `apps/api/app/journal/repo.py` (raw SQL where pgvector is involved; SQLAlchemy 2 elsewhere).
- **Tool pattern**: `apps/api/app/agent/tools/dominance.py` (closest equivalent — single provider tool).
- **Validator extension pattern**: `apps/api/app/agent/validators.py::must_cite_quantitative_claims`.
- **Telegram inline keyboard pattern**: `apps/api/app/notifications/telegram.py::_inline_kb_for_setup`.

When in doubt about a convention, find the closest existing module and mirror it. Module docs are in `docs/architecture/*.md`.
</reference_existing_modules>
