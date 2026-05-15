# Trading Copilot — Project-Level Guidance for Claude Code

<context>
Trading Copilot is a single-user autonomous trading system for crypto perps. The operator is a full-stack AI developer in Madrid. Capital: €1.000 learning + return, infra costs separate. Venue: Hyperliquid (live) + Binance testnet (paper). The architecture is documented across 12 module docs in `docs/architecture/*.md`. This file holds project-wide rules that apply to ALL modules. Read it before any task.
</context>

<scope>
This file is the entry point for Claude Code in this repository. Module-specific specs live under `docs/specs/<module>/` and override these defaults only when explicitly stated. If a module spec contradicts CLAUDE.md, the module spec wins, but flag the contradiction in your output.
</scope>

<critical_invariants>
These never break. A PR that violates any of them is rejected.

1. **TZ-aware UTC everywhere.** `datetime.now(tz=UTC)`, never naive. Validators reject naive `expires_at`.
2. **`user_id` in every DB query.** Even single-user — defense in depth. Repo functions take `user_id` as required kwarg, not optional.
3. **System prompts are byte-stable.** Never interpolate `datetime.now()`, per-request data, or live counters into `system_prompt.py`. Per-request data goes in user messages.
4. **Tools registered in alphabetical order in `build_agent()`.** Reordering invalidates Anthropic prompt cache.
5. **Citations by `tool_name`, never by opaque `tool_call_id`.** LLMs can't echo provider-side UUIDs reliably.
6. **No-lookahead in indicators and backtests.** Extending the time series must NOT mutate previously computed values. Tested in `tests/indicators/test_no_lookahead.py`.
7. **`is_closed` derived locally in `normalize_ccxt_ohlcv`.** Don't trust the exchange flag; compute it from `candle_end <= now`.
8. **`stop_loss` naming end-to-end.** `TradeIdea.stop_loss` ↔ `journal_trades.stop_loss_px` ↔ `OpenSetupRow.stop_loss_px`. Never `invalidation_px` (that's a different concept: pre-entry invalidation conditions).
9. **`entry_sl_px` persisted to `risk_state` at activation.** Required for correct r_multiple post-BE.
10. **Cardinality discipline in Prometheus.** Never `user_id`, `setup_id`, `trade_id` as labels. Only bounded enums.
11. **No tokens or secrets in logs.** If presence must be logged, use `token_len=len(token)`, never `token_prefix=token[:6]`.
12. **Eager singleton + `asyncio.Lock` for agents.** `get_agent()` returns a cached instance; `get_agent_async()` uses a lock to avoid race in cold start.
</critical_invariants>

<tech_stack_constraints>
Versions and choices are not negotiable without an ADR:

- **Python**: 3.12+
- **Web framework**: FastAPI
- **ORM**: SQLAlchemy 2 async + asyncpg
- **DataFrames**: Polars (never pandas in new code; pandas only where legacy code already uses it)
- **HTTP client**: `httpx` async (never `requests`; never `aiohttp`)
- **DB**: PostgreSQL 16 + TimescaleDB extension + pgvector
- **Cache/pubsub**: Valkey (Redis wire-compatible) via `redis-py` async
- **Agent framework**: `pydantic-ai`
- **LLM gateway**: OpenRouter in dev/paper, Anthropic direct in live (DR fallback to OpenRouter)
- **Models**:
  - Scout: `claude-haiku-4-5-20251001`
  - Chat + Supervisor: `anthropic/claude-sonnet-4-6`
  - On-demand audit: `anthropic/claude-opus-4-7` (manual only)
- **Embeddings**: `voyage-3-large` (1024-dim), but **deferred** until journal ≥ 200 entries
- **Exchange access**: `ccxt.pro` for Binance + Bybit; custom client for Hyperliquid (no CCXT support for clearinghouseState)
- **Tests**: `pytest` + `pytest-asyncio` (mode=auto) + `hypothesis` for property-based
- **Logging**: `structlog` with JSON renderer in production
- **Metrics**: `prometheus_client` with a single global registry in `core/observability/metrics.py`
- **Money in code**: `Decimal` with `getcontext().prec = 28`, `ROUND_HALF_EVEN`. Never `float` for monetary values. `float` allowed in bps/percentage scale.
</tech_stack_constraints>

<file_layout_rules>
- Module roots under `apps/api/app/<module>/`
- Tests under `apps/api/tests/<module>/`
- Migrations under `apps/api/alembic/versions/NNN_descriptive_name.py` (3-digit prefix, monotonically increasing)
- Module docs under `docs/architecture/<module>.md` (one per module; this is what you already have)
- Specs for new work under `docs/specs/<module>/` (specs for implementing or extending modules)

When creating a new module, mirror the layout of `apps/api/app/market/`:
```
<module>/
├── __init__.py
├── models.py              # Pydantic models
├── repo.py                # Raw SQL or SQLAlchemy 2 async
├── service.py             # Business logic (aggregators, orchestrators)
├── runtime.py             # Lifespan-managed tasks (only if module has background loops)
├── routes.py              # FastAPI router (only if module has HTTP endpoints)
├── tool.py                # pydantic-ai tool registration (only if exposed to agent)
└── providers/             # When module has multiple external data sources
    ├── base.py            # ABC
    └── <provider>.py
```
</file_layout_rules>

<agent_tool_rules>
Every new tool registered on the agent MUST:

1. Take `ctx: RunContext[AgentDeps]` as first parameter.
2. Return `ToolResult[T]` (from `agent/tools/_envelope.py`), with `Provenance(source, as_of, rows, warnings)`.
3. Have a docstring with `USE THIS WHEN:` and `DO NOT USE THIS TO:` sections. The LLM reads this; treat as semver.
4. Be registered alphabetically in `agent/agent.py::build_agent()`.
5. Have a corresponding validator extension in `agent/validators.py` that enforces snapshot verification (each numeric claim in `ToolCitation.snapshot` must match the real tool output within 0.1% tolerance).
6. Scope every DB query by `deps.user_id`.

`AgentDeps` signature is fixed:
```python
@dataclass
class AgentDeps:
    session_factory: async_sessionmaker[AsyncSession]
    log: structlog.BoundLogger
    user_id: str
    exchange: str = "binance_usdm"
```
</agent_tool_rules>

<error_handling_conventions>
- Network errors: retry with `tenacity` (max 4 attempts, exponential jitter, max delay 30s). Wrap in `ToolResult` with `warnings=['retry_exhausted']` on final fail; never raise to caller.
- DB errors inside tools: log + return `ToolResult` with empty data + warning. Never let DB errors propagate to the LLM (they'd appear in traces and cost tokens explaining themselves).
- Validation errors from `pydantic-ai`: raise `ModelRetry(reason)` with a short, actionable reason. Max retries = 2 (already configured at agent level).
- Stale data: every provider tagged with `max_age_seconds`. Service layer marks `degraded` if all sources stale; `warning` if some.
- Exchange API outages: fail-closed (no operations). Update Prometheus gauge `mt_runtime_streams_alive`.
</error_handling_conventions>

<testing_conventions>
- All `pytest` files under `apps/api/tests/<module>/test_*.py`.
- Use `pytest-asyncio` mode=auto (set in `pyproject.toml`).
- DB tests use `tests/conftest.py` fixtures with transactional rollback per test.
- Property-based tests with `hypothesis` for any pure-function module (especially RiskManager, indicators, factor stats).
- Network tests guarded with `@pytest.mark.integration`; default `pytest` run excludes them via `-m "not integration"`.
- Citation contract tests: for every new tool, add `test_validator_rejects_phantom_<tool>` to `tests/agent/test_validators_citation_rigor.py`.
- Coverage targets are not enforced numerically, but PRs that delete tests need explicit justification.
</testing_conventions>

<money_and_risk_constants>
Read from `core/config.Settings` — never hardcode:

- `risk_per_trade_pct`: 0.75 (% equity)
- `max_leverage_per_position`: 3
- `max_gross_leverage`: 1.5
- `daily_loss_limit_pct`: 3.0 (% equity, freeze 24h on hit)
- `max_drawdown_circuit_pct`: 10.0 (% from HWM, manual unlock required)
- `cooldown_sl_streak_2`: 4 (hours)
- `cooldown_sl_streak_3`: 24 (hours)
- `min_rr_ratio`: 1.5
- `min_factor_lcb`: 0.42
- `min_expectancy_lcb_r`: 0.25
- `bayesian_prior_alpha`: 2.0 (Beta prior for factor win-rate)
- `bayesian_prior_beta`: 2.5
- `approval_timeout_seconds`: 90

These are gates. Tests verify they are read from Settings, not hardcoded.
</money_and_risk_constants>

<commit_and_pr_conventions>
- Conventional commits: `feat(<module>): ...`, `fix(<module>): ...`, `refactor(<module>): ...`, `test(<module>): ...`, `docs(<module>): ...`, `chore: ...`.
- Each PR includes:
  - Description of the change (1-3 paragraphs)
  - Tests added (link to test file)
  - Invariants verified (list which `<critical_invariants>` points apply)
  - Telemetry impact (any new metric, log field, or Grafana panel)
- Migrations go in their own PR, never bundled with code that depends on them.
- Specs in `docs/specs/<module>/` are version-controlled. Update the spec in the same PR if the implementation deviates.
</commit_and_pr_conventions>

<communication_style>
When responding to a task in this repo:

1. Start by stating which spec(s) you are implementing.
2. List files you plan to create or modify, with one-line rationale each.
3. State any deviations from the spec and why.
4. Implement.
5. Run tests; report results.
6. Summarize changes with paths and line counts.

Don't ask clarifying questions if the spec answers them. If the spec is ambiguous, propose a default (with rationale) and proceed; flag the assumption in your summary.
</communication_style>

<security_and_secrets>
- Secrets via env vars (`.env` for dev, Railway env for production). Never commit `.env`.
- `Settings.openrouter_api_key`, `anthropic_api_key`, `voyage_api_key`, `coinglass_api_key`, `telegram_bot_token`, `telegram_webhook_secret`, exchange API keys — all `SecretStr`.
- Webhook secrets verified with `secrets.compare_digest` (timing-safe).
- HMAC signatures use constant-time comparison.
- Logs scrub: any field name matching `*key*`, `*token*`, `*secret*`, `*password*` is automatically redacted in `structlog` config.
</security_and_secrets>

<gotchas>
- Hyperliquid uses coin symbols without quote (`BTC`, `ETH`, `SOL`) — NOT `BTCUSDT`. There's a mapping table at `core/exchanges/hyperliquid_symbols.py` (to be created).
- Binance USDM uses `BTCUSDT`. CCXT spot uses `BTC/USDT` with slash.
- Coinglass API uses `symbol=BTC`, not `BTCUSDT`. Different from Binance.
- `voyage-3-large` requires `input_type='document'` for indexing and `input_type='query'` for retrieval. Different conditioning. Don't mix.
- TimescaleDB chunk_time_interval is set at table creation. Migration must check `pg_available_extensions` before `CREATE EXTENSION IF NOT EXISTS timescaledb`.
- pgvector cosine ops uses `<=>` operator. Cosine similarity = 1 - cosine distance. Always use the operator, not raw math.
- `pydantic-ai` Agent retries are configured at construction time; can't be changed per-call.
- OpenRouter adds 50-150ms latency vs Anthropic direct; for sub-second approval flows, use Anthropic direct.
- `Decimal(float)` introduces float artifacts. Always `Decimal(str(x))` when converting from float.
- Polars LazyFrame is evaluated on `.collect()`. Don't `.collect()` until the DB boundary.
- pgvector indexes (HNSW) require `vector(N)` type with fixed N. Changing dimensions = drop+recreate index.
</gotchas>

<links>
Module architecture docs (existing code):
- `docs/architecture/agent.md`
- `docs/architecture/alerts.md`
- `docs/architecture/backtest.md`
- `docs/architecture/core.md`
- `docs/architecture/journal.md`
- `docs/architecture/market.md`
- `docs/architecture/notifications.md`
- `docs/architecture/paper_trading.md`
- `docs/architecture/platform_routes.md`
- `docs/architecture/post_mortem.md`
- `docs/architecture/reviewer.md`
- `docs/architecture/setups.md`

Specs for new work (this Cerebro 1 effort):
- `docs/specs/liquidation/00_OVERVIEW.md`
- `docs/specs/liquidation/01_MODELS_AND_SCHEMA.md`
- `docs/specs/liquidation/02_PROVIDER_A_DERIVED.md`
- `docs/specs/liquidation/03_PROVIDER_B_HYPERLIQUID.md`
- `docs/specs/liquidation/04_HEATMAP_SERVICE.md`
- `docs/specs/liquidation/05_AGENT_TOOL_AND_VALIDATOR.md`
- `docs/specs/liquidation/06_TELEGRAM_INTEGRATION.md`
- `docs/specs/liquidation/07_TESTING.md`
- `docs/specs/liquidation/08_IMPLEMENTATION_ORDER.md`
</links>
