# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Crypto Trading Copilot. The LLM is interpreter and orchestrator, never an oracle — every quantitative claim must cite a deterministic tool. Single-user system targeting Hyperliquid (live) + Binance USDM (testnet/paper). Design rationale lives in `docs/Crypto Trading Copilot 2026_ Opinionated Technical Blueprint.md`; phase plans live in `.claude/plans/`.

Monorepo (pnpm + turbo): `apps/api` (FastAPI, Python 3.13, uv) and `apps/web` (Next.js 16, React 19, Tailwind 4). Data plane is Postgres 18 + TimescaleDB + pgvector + Valkey via `docker-compose.yml`.

**Project-wide invariants, tech stack constraints, and tool/agent conventions live in `docs/cerebro1/CLAUDE.md` (`<critical_invariants>`, `<tech_stack_constraints>`, `<agent_tool_rules>`, `<gotchas>`).** Read it before any non-trivial task; the rules there override generic defaults. The 12 critical invariants there are the hard contract — violations get PRs rejected.

## Common commands

Quickstart (root): `pnpm install && docker compose up -d` then run backend + frontend in separate terminals.

**Backend** (run from `apps/api`, all commands prefixed with `uv run`):
- `uv sync` — install / refresh venv
- `uv run alembic upgrade head` — apply migrations (head is `028`; versions in `alembic/versions/`)
- `uv run alembic revision -m "desc"` — new migration; use bare numeric `revision = "029"` and `down_revision = "028"` to match the repo convention (the autogenerate ID prefix is **not** what we use here)
- `uv run uvicorn app.main:app --reload --port 8000` — dev server
- `uv run python -m app.market.ohlcv.backfill --symbol BTCUSDT --tf 1m --years 2` — historical OHLCV backfill
- `uv run pytest` — full suite (e.g. `pytest tests/liquidation/test_models.py::test_naive_datetime_rejected` for a single test). `asyncio_mode = "auto"` is set, so async tests don't need decorators.
- `uv run ruff check .` / `uv run ruff format .` — lint/format
- `uv run mypy app` — strict type-check

If a Bash invocation that pipes through pytest fails to find `uv`, activate the venv first: `source /Users/samuelperez/trading/apps/api/.venv/bin/activate`.

**Frontend** (run from `apps/web`):
- `pnpm dev` — Next.js dev (Turbopack, port 3000; another local Next.js process tends to own 3000 so the dev server may land on 3001)
- `pnpm typecheck` / `pnpm lint` / `pnpm build`

**Workspace** (root): `pnpm dev` / `pnpm build` / `pnpm typecheck` / `pnpm lint` run via Turborepo across both apps.

## Architecture

### Agent loop (`apps/api/app/agent/`)
- `agent.py` builds a singleton `pydantic_ai.Agent` with `OpenRouterModel` (default `anthropic/claude-sonnet-4.6`, `thinking="medium"`, `max_tokens=24000`). The provider is constructed explicitly with the API key from `Settings` — *not* from `os.environ` — so it works regardless of how uvicorn is launched.
- The agent's `output_type` is a union `BriefAnalysis | TradeIdea | str`. Pydantic-AI re-prompts the model on validation failure (`retries=2`).
- Tools are registered in **alphabetical order** in `build_agent()` (cache-prefix stability). Each tool returns `ToolResult[T]` with `Provenance(source, as_of, rows, warnings)`; the LLM cites tools by `tool_name`, never by provider tool-call IDs.
- `validators.py` enforces three contracts on every output: (1) every citation's `tool_name` matches a tool actually called this turn, (2) any `run_id` / `trade_id` in `snapshot` exists in the tool's real return value, and (3) per-tool snapshot-rigor checks (see `_verify_liquidation_citation` for the directional-zone pattern). Violations raise `ModelRetry`.
- `system_prompt.py` builds frozen ordered blocks (tools catalog → copilot rules → trader profile JSON). Do NOT interpolate timestamps or per-request data into the system prompt — it would invalidate Anthropic prompt caching. Per-request data goes in the user message.
- Tools receive `RunContext[AgentDeps]`; `AgentDeps` carries `session_factory`, structlog logger, and `user_id`. All DB queries scope by `user_id`.

### Review agent (`apps/api/app/reviewer/`)
- A **second, independent** pydantic-AI agent for post-entry trade reviews — does NOT extend the main agent's `output_type` union. Same model but `thinking="low"`, `max_tokens=8000`; its own system prompt is ~5× shorter for better cache hits and a smaller tool set (no journal/backtest tools).
- Dispatch is centralized in `app/reviewer/dispatcher.py::maybe_run_review`. `SetupRuntime` calls it on triggers (time offset, price move, approaching SL, entry hit). The dispatcher handles atomic cooldown claim, global concurrency semaphore (`REVIEW_CONCURRENCY`), persistence (`setup_reviews` + `setup_events`), and pub/sub fan-out on channel `reviews:user:{user_id}`.

### Post-mortem agent (`apps/api/app/post_mortem/`)
- Third independent pydantic-AI agent for closed-trade analyses (`POST_MORTEM_MODEL_ID = "anthropic/claude-sonnet-4.6"`). Dispatcher pattern mirrors the reviewer.

### Scout (autonomous) (`apps/api/app/setups/scout_dispatcher.py`)
- Lighter-weight Haiku-driven path that turns alert hits with `is_scout_trigger=TRUE` into trade-idea proposals. Cooldown via `app/alerts/cooldown.py::should_pause_scout` (SL streaks pause the scout per user/symbol).
- Direction is "autonomous bot 24/7 with human-in-the-loop": scout proposes → user approves via Telegram → setup transitions.

### Chat transport (`apps/api/app/agent/routes.py` ↔ `apps/web/components/chat/CopilotChat.tsx`)
- Backend: `POST /chat` uses `VercelAIAdapter.dispatch_request` to stream the AI SDK Data Stream Protocol. The response sets `Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no`, `Connection: keep-alive` to prevent SSE buffering through Railway-edge / proxies.
- Frontend: `@ai-sdk/react`'s `useChat` with `DefaultChatTransport`. The transport injects `Authorization: Bearer <token>` (read from localStorage) so chat works cross-domain. `expose_headers` includes `x-vercel-ai-ui-message-stream` for AI SDK protocol detection.

### Auth (BetterAuth)
- Single Postgres `session` table is the source of truth. Next.js writes via `apps/web/lib/core/auth/auth.ts` (`betterAuth` + `bearer()` plugin); FastAPI reads in `apps/api/app/core/auth/session.py`.
- Two transports for the same token: cookie `better-auth.session_token` (same-origin) **or** `Authorization: Bearer <token>` (cross-domain Vercel↔Railway). FastAPI prefers the bearer header. The `<token>.<hmac>` suffix is stripped before the row lookup; HMAC is not re-verified in Python — the `expiresAt > now()` row check is the gate.
- All API calls from the browser go through `apps/web/lib/core/api.ts::apiFetch`, which attaches the bearer token from `localStorage[mt.bearer_token]` and redirects to `/auth/login` on 401.
- `apps/web/middleware.ts` redirects unauthenticated users to `/auth/login` based only on cookie presence (cheap); real auth happens in FastAPI.

### Data plane
- **TimescaleDB hypertables**: OHLCV (migration `001`) and `market_trades` (migration `027`, per-trade granular for Provider A of the liquidation engine).
- Backfill via CCXT (`app/market/ohlcv/backfill.py`); live ingestion (`LiveIngestion` in `app/main.py` lifespan) runs both `_watch_ohlcv_loop` and `_watch_trades_loop`. `WATCH_SYMBOLS` env (CSV) defines which USDT-M perps stream + persist; the frontend's `WATCH_SYMBOLS` constant in `apps/web/lib/store/active-symbol.ts` **must** match — symbols not in the backend list return empty OHLCV.
- The trades loop **filters zero-price/zero-size records** before insert (ccxt.pro emits synthetic ones that violate the CHECK constraint and tumble whole batches — see migration 027 + the loop in `ingestion_live.py`).
- **pgvector** for journal embeddings (voyage-3-large, 1024 dim; deferred until journal ≥ 200 entries) — backfilled offline by `scripts/embed_backfill.py`.
- **Valkey** (Redis-compatible) for pub/sub fan-out from backend → frontend WS clients (`app/core/broadcasting/pubsub.py`). `redis-py` against Valkey — wire-compatible.
- **Async SQLAlchemy 2** with `asyncpg`. The `DATABASE_URL` field-validator auto-promotes `postgres://` / `postgresql://` to `postgresql+asyncpg://` so any hosted Postgres URL works without manual edits.
- `app/core/db.py` exposes both `session_scope()` (caller-managed transaction; commits on success) and `session_dependency()` (FastAPI-style per-request session).

### Liquidation engine — Cerebro 1 (`apps/api/app/liquidation/`)
- First of seven planned "cerebros" (specialized agent toolheads). Aggregates two providers into a `HeatmapSnapshot` of magnet zones:
  - **Provider A — `DerivedLiquidationProvider`** (`providers/derived.py`): Polars-vectorized over `market_trades` with 7 leverage brackets and exponential time decay. `_compute_buckets` is a pure function on `(DataFrame, now)` to guarantee no-lookahead (tested by `test_no_lookahead`).
  - **Provider B — `HyperliquidLiquidationProvider`** (`providers/hyperliquid.py`): reads real `clearinghouseState` per address. Address universe bootstrap via WS trades + 6h leaderboard refresh (the leaderboard endpoint is undocumented and returns 422 in the wild — handled gracefully; the WS path is primary).
- `HeatmapService` (`service.py`) calls providers in parallel with a 3s timeout, loads weights (uniform M1 / calibrated M2), grid-snaps and merges buckets, computes imbalance/density/agreement/confidence, and persists raw buckets.
- `compute_provider_weights` (`calibration.py`) is ready for the M2 weekly cron; floors at 0.10 raw rate, normalizes per `(symbol, timeframe)`. Pydantic `ProviderWeight.weight` is `ge=0.0` and DB CHECK allows `[0.0, 1.0]` (migration `028` relaxed the floor to match post-normalization values).
- Agent tool `get_liquidation_heatmap` is registered alphabetically in both `build_agent()` and `build_review_agent()`. The dedicated validator `_verify_liquidation_citation` in `agent/validators.py` enforces directional zone semantics (long → short_liq TP, short → long_liq TP), agreement match within 0.001, current_price within 0.5%, and confidence/agreement coherence.
- Telegram ground-truth: when `Settings.ground_truth_collection_enabled=True`, setup alerts render a magnet-zone preview plus three inline buttons (`gt:agree|close|disagree`); `liquidation/telegram_handlers.py::record_ground_truth` persists rows to `liquidation_agreement_log` with computed deltas. Toggle to `False` at start of M2 once weights are calibrated. (Until `insert_setup_from_idea` is extended to persist the heatmap citation into `journal_trades.factor_snapshot.get_liquidation_heatmap`, the handler logs `gt_no_heatmap_citation` and returns False without crashing — tracking issue open.)
- Metrics (bounded enums only): `mt_liq_snapshots_total{symbol,timeframe,outcome}`, `mt_liq_snapshot_latency_seconds{provider}` (histogram), `mt_liq_active_addresses` (gauge), `mt_liq_provider_errors_total{provider,kind}`. Grafana dashboard at `infra/grafana/dashboards/liquidation-overview.json`.
- Detailed module doc at `docs/architecture/liquidation.md`; complete spec stack at `docs/specs/liquidation/`.

### Frontend state shape
- **Active symbol/timeframe**: zustand store in `apps/web/lib/store/active-symbol.ts`, persisted to localStorage. Sidebar + chart read; chat writes via tool `input.symbol` (`useSymbolBridge.ts`) — no regex parsing.
- **Chart overlays**: zustand store in `apps/web/lib/store/chart-overlays.ts`. Indicators (EMA/SMA/BB/VWAP) are user preferences (persisted); structure + tradeIdea overlays are ephemeral per-agent-turn. Indicators are computed in the frontend; trade SL/TP zones use `BaselineSeries`.
- **Path aliases**: `@/components`, `@/lib`, `@/hooks`, `@/components/ui` (shadcn config in `components.json`, style `radix-nova`). Use them; do not write relative `../../` paths.

### Backtest stack (F2) (`apps/api/app/backtest/`)
- OSS-only: `skfolio` for CombinatorialPurgedCV; DSR / PSR / PBO are implemented from Bailey & López de Prado in `app/backtest/metrics.py`. Walk-forward and CPCV runners live next to it.
- The Mertens variance term + per-observation Sharpe convention are easy to mis-implement — see existing tests in `tests/backtest/` before changing those formulas.

### Alerts (F3) (`apps/api/app/alerts/`)
- DSL parser in `dsl.py`, evaluator in `evaluator.py`. Long-running `AlertsRuntime` started in lifespan; fans events out via Postgres `LISTEN/NOTIFY` (raw `asyncpg` — see `mypy` overrides in pyproject) into Valkey pub/sub.
- The OHLCV+indicators panel-builder is factored into `panel_service.py` so both `AlertsRuntime` and `SetupRuntime` evaluate rules against an identically-shaped panel. The `_max_lookback` heuristic (Wilder smoothing × 2 + cross headroom, floored at 60) is the easy thing to break — see the docstring before tuning.

### Setups lifecycle (`apps/api/app/setups/`)
- `SetupRuntime` (lifespan-managed) tracks pending → active → closed transitions on candle close. When the agent emits a `TradeIdea`, validators call `insert_setup_from_idea` to materialize it; the runtime watches price and updates `setups` + `setup_events`.
- **Invalidation vs cancel**: pending setups carry `invalidation_conditions jsonb` (DSL conditions reusing `app.alerts.dsl`) and optional `expires_at`. The first to fire transitions `pending → cancelled` with `setup_events.event = 'invalidated'` (distinct from `'cancelled'` = manual user cancel). **Naming gotcha**: `journal_trades.invalidation_px` was renamed `stop_loss_px`; `TradeIdea.invalidation` became `TradeIdea.stop_loss`. "Invalidation" is now reserved for the pre-entry concept.
- **Thesis persistence**: `journal_trades` stores `summary_es_full` (verbatim copy, ≤1100 chars), `confluences` and `scenarios` as JSONB. This lets the review agent judge "does the thesis still hold?" without re-deriving via tools.

### Paper trading (`apps/api/app/paper_trading/`)
- F4 module — `engine.py`, `positions.py`, `repo.py`. Migration `024_paper_trading_engine`. Live execution path is intentionally absent for v1 (the agent has no `place_order` tool).

## Module docs

One per backend module at `docs/architecture/<module>.md`: agent, alerts, backtest, core, journal, liquidation, market, notifications, paper_trading, platform_routes, post_mortem, reviewer, setups. Read the relevant one before editing a module.

## Environment variables

`apps/api/.env.example` is the canonical list. Most important:
- `DATABASE_URL` — accepts `postgres://`, `postgresql://`, or `postgresql+asyncpg://` (auto-promoted).
- `WATCH_SYMBOLS` — CSV. Must be kept in sync with `apps/web/lib/store/active-symbol.ts::WATCH_SYMBOLS` or symbols silently return empty OHLCV.
- `OPENROUTER_API_KEY` — required for chat (`/health` reports `openrouter: missing` when absent).
- `VOYAGE_API_KEY` — required for embeddings.
- `BETTER_AUTH_SECRET`, `BETTER_AUTH_URL`, `GOOGLE_CLIENT_ID/SECRET` — Next.js. Google OAuth auto-disables when client id/secret missing; email+password always works.
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` — Telegram alert + ground-truth flow.
- `GROUND_TRUTH_COLLECTION_ENABLED` — flips Cerebro 1 GT button rendering. Default `True` during M1; flip to `False` at start of M2.
- `REVIEW_*` — review agent tuning: `REVIEW_CONCURRENCY` (default 2), `REVIEW_MAX_REVIEWS_PER_SETUP` (default 12), `REVIEW_COOLDOWN_MIN_MINUTES`, `REVIEW_TIME_OFFSETS_H` (CSV), `REVIEW_PRICE_MOVE_PCT`, `REVIEW_APPROACHING_SL_PCT`, `REVIEW_PRICE_*` (per-million token pricing for cost telemetry).

## Things that look unusual but are intentional

- Alembic revision IDs are bare numerics (`"027"`, `"028"`) — not `"028_descriptive_name"`. The descriptive part is the filename only. New migrations must follow this; spec text saying otherwise is out of date (see migration 026 deviation in `CHANGELOG.md`).
- `ruff` ignores `E741` (`l` is canonical for "low" in OHLCV across the entire crypto API ecosystem) and `RUF002/003` (Greek letters / math operators in indicator docstrings are deliberate).
- `mypy strict = true` but `disallow_untyped_decorators = false` for FastAPI; `ccxt`, `skfolio`, `pandas`, `asyncpg` carry explicit `ignore_missing_imports`.
- The agent does NOT have `place_order` and will not for v1. The product is an advisor; live execution comes only after paper-trading is verified.
- HyperliquidClient runs HTTP/1.1 only (`http2=True` removed) to avoid the optional `h2` dependency. HTTP/1.1 is sufficient for one-shot POSTs.
- The frontend uses ai-elements (a separate component family from shadcn/ui) for chat UI primitives in `components/ai-elements/`. Both coexist; ai-elements is the right choice for anything chat-shaped.
- `docs/cerebro1/CLAUDE.md` is named for Cerebro 1 but its `<critical_invariants>`, `<tech_stack_constraints>` and `<agent_tool_rules>` are project-wide. That's the source of truth for those rules.
