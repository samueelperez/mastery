# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Crypto Trading Copilot. The LLM is interpreter and orchestrator, never an oracle — every quantitative claim must cite a deterministic tool. Design rationale lives in `docs/Crypto Trading Copilot 2026_ Opinionated Technical Blueprint.md`; phase plans live in `.claude/plans/`.

Monorepo (pnpm + turbo): `apps/api` (FastAPI, Python 3.13, uv) and `apps/web` (Next.js 16, React 19, Tailwind 4). Data plane is Postgres 18 + TimescaleDB + pgvector + Valkey via `docker-compose.yml`.

## Common commands

Quickstart (root): `pnpm install && docker compose up -d` then run backend + frontend in separate terminals.

**Backend** (run from `apps/api`, all commands prefixed with `uv run`):
- `uv sync` — install / refresh venv
- `uv run alembic upgrade head` — apply migrations (versions in `alembic/versions/`)
- `uv run alembic revision -m "desc" --autogenerate` — create new migration
- `uv run uvicorn app.main:app --reload --port 8000` — dev server
- `uv run python -m app.market.ohlcv.backfill --symbol BTCUSDT --tf 1m --years 2` — historical OHLCV backfill
- `uv run pytest` — full suite (`pytest tests/indicators/test_core.py::test_ema_no_lookahead` for a single test). `asyncio_mode = "auto"` is set, so async tests don't need decorators.
- `uv run ruff check .` / `uv run ruff format .` — lint/format
- `uv run mypy app` — strict type-check

**Frontend** (run from `apps/web`):
- `pnpm dev` — Next.js dev (Turbopack, port 3000; another local Next.js process tends to own 3000 so the dev server may land on 3001)
- `pnpm typecheck` / `pnpm lint` / `pnpm build`

**Workspace** (root): `pnpm dev` / `pnpm build` / `pnpm typecheck` / `pnpm lint` run via Turborepo across both apps.

## Architecture

### Agent loop (`apps/api/app/agent/`)
- `agent.py` builds a singleton `pydantic_ai.Agent` with `OpenRouterModel` (default `anthropic/claude-sonnet-4.6`, `thinking="medium"`, `max_tokens=24000`). The provider is constructed explicitly with the API key from `Settings` — *not* from `os.environ` — so it works regardless of how uvicorn is launched.
- The agent's `output_type` is a union `BriefAnalysis | TradeIdea | str`. Pydantic-AI re-prompts the model on validation failure (`retries=2`).
- Tools are registered in alphabetical order (cache-prefix stability) from `app/agent/tools/*.py`. Each tool returns a typed Pydantic envelope including a `source` and `as_of`; the LLM cites tools by `tool_name`, never by provider tool-call IDs.
- `validators.py` enforces two contracts on every output: (1) every citation's `tool_name` matches a tool actually called this turn, and (2) any `run_id` / `trade_id` in `snapshot` exists in the tool's real return value. Violations raise `ModelRetry`.
- `system_prompt.py` builds frozen ordered blocks (tools catalog → copilot rules → trader profile JSON). Do NOT interpolate timestamps or per-request data into the system prompt — it would invalidate Anthropic prompt caching. Per-request data goes in the user message.
- Tools receive `RunContext[AgentDeps]`; `AgentDeps` carries a `session_factory` (use `async with deps.session_factory() as session`), a structlog logger, and `user_id`. All DB writes scope by `user_id`.

### Review agent (`apps/api/app/reviewer/agent.py`)
- A **second, independent** pydantic-AI agent for post-entry trade reviews — does NOT extend the main agent's `output_type` union. Same model (`anthropic/claude-sonnet-4.6`) but `thinking="low"` and `max_tokens=8000` because the schema is bounded; its own system prompt (`reviewer/system_prompt.py`) is ~5× shorter for better cache hits, and only ~7 tools registered (ohlcv, indicators, structure, confluence, correlation, perps_data, volume_profile) — no journal/backtest tools.
- Dispatch is centralized in `app/reviewer/dispatcher.py::maybe_run_review`. The `SetupRuntime` calls it when a trigger fires (time offset, price move, approaching SL, entry hit, etc.). The dispatcher handles: atomic cooldown claim (`claim_review_slot` is a conditional UPDATE), global concurrency semaphore (`REVIEW_CONCURRENCY`), persistence (`setup_reviews` + `setup_events`), and pub/sub fan-out on channel `reviews:user:{user_id}`.
- Reviews persist via `app/reviewer/repo.py`. Each review is rate-limited per setup (`REVIEW_MAX_REVIEWS_PER_SETUP`, default 12) with an exponential cooldown (`REVIEW_COOLDOWN_MIN_MINUTES`).

### Chat transport (`apps/api/app/agent/routes.py` ↔ `apps/web/components/chat/CopilotChat.tsx`)
- Backend: `POST /chat` uses `VercelAIAdapter.dispatch_request` to stream the AI SDK Data Stream Protocol. The response sets `Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no`, `Connection: keep-alive` to prevent SSE buffering through Railway-edge / proxies.
- Frontend: `@ai-sdk/react`'s `useChat` with `DefaultChatTransport`. The transport injects `Authorization: Bearer <token>` (read from localStorage) so chat works cross-domain. `expose_headers` includes `x-vercel-ai-ui-message-stream` for the AI SDK protocol detection.

### Auth (BetterAuth)
- Single Postgres `session` table is the source of truth. Next.js writes via `apps/web/lib/core/auth/auth.ts` (`betterAuth` + `bearer()` plugin); FastAPI reads in `apps/api/app/core/auth/session.py`.
- Two transports for the same token: cookie `better-auth.session_token` (same-origin) **or** `Authorization: Bearer <token>` (cross-domain Vercel↔Railway). FastAPI prefers the bearer header. The `<token>.<hmac>` suffix is stripped before the row lookup; HMAC is not re-verified in Python — the `expiresAt > now()` row check is the gate.
- All API calls from the browser go through `apps/web/lib/api.ts::apiFetch`, which attaches the bearer token from `localStorage[mt.bearer_token]` and redirects to `/auth/login` on 401.
- `apps/web/middleware.ts` redirects unauthenticated users to `/auth/login`. It only checks the cookie's presence (cheap) — actual auth happens in FastAPI.

### Data plane
- **TimescaleDB hypertable** for OHLCV (migration `001`). Backfill via CCXT (`app/market/ohlcv/backfill.py`); live via `LiveIngestion` started in the FastAPI lifespan (`app/main.py`). `WATCH_SYMBOLS` env (CSV) defines which USDT-M perps stream + persist; the frontend's `WATCH_SYMBOLS` constant in `apps/web/lib/store/active-symbol.ts` **must** match — symbols not in the backend list return empty OHLCV.
- **pgvector** for journal embeddings (voyage-4-large, 1024 dim) — backfilled offline by `scripts/embed_backfill.py`.
- **Valkey** (Redis-compatible) for pub/sub fan-out from backend → frontend WS clients (`app/core/broadcasting/pubsub.py`). We use `redis-py` against Valkey — wire-compatible.
- **Async SQLAlchemy 2** with `asyncpg`. The `DATABASE_URL` field-validator auto-promotes `postgres://` / `postgresql://` to `postgresql+asyncpg://` so any hosted Postgres URL works without manual edits.
- `app/core/db.py` exposes both `session_scope()` (caller-managed transaction; commits on success) and `session_dependency()` (FastAPI-style per-request session).

### Frontend state shape
- **Active symbol/timeframe**: zustand store in `lib/store/active-symbol.ts`, persisted to localStorage. Sidebar + chart read; chat writes via tool `input.symbol` (`useSymbolBridge.ts`) — no regex parsing.
- **Chart overlays**: zustand store in `lib/store/chart-overlays.ts`. Indicators (EMA/SMA/BB/VWAP) are user preferences (persisted); structure + tradeIdea overlays are ephemeral per-agent-turn. Indicators are computed in the frontend; trade SL/TP zones use `BaselineSeries`.
- **Path aliases**: `@/components`, `@/lib`, `@/hooks`, `@/components/ui` (shadcn config in `components.json`, style `radix-nova`). Use them; do not write relative `../../` paths.

### Backtest stack (F2)
- OSS-only: `skfolio` for CombinatorialPurgedCV; DSR / PSR / PBO are implemented from Bailey & López de Prado in `app/backtest/metrics.py`. Walk-forward and CPCV runners live next to it.
- The Mertens variance term + per-observation Sharpe convention are easy to mis-implement — see existing tests in `tests/backtest/` before changing those formulas.

### Alerts (F3)
- DSL parser in `app/alerts/dsl.py`, evaluator in `app/alerts/evaluator.py`. Long-running runtime `AlertsRuntime` started in lifespan; fans events out via Postgres `LISTEN/NOTIFY` (uses raw `asyncpg` — see `mypy` overrides in pyproject) into Valkey pub/sub.
- The OHLCV+indicators panel-builder is factored into `app/alerts/panel_service.py` so both `AlertsRuntime` and `SetupRuntime` evaluate rules against an identically-shaped panel. The `_max_lookback` heuristic (Wilder smoothing × 2 + cross headroom, floored at 60) is the easy thing to break — see the docstring before tuning.

### Setups lifecycle (`F3.5` → F4)
- `SetupRuntime` (lifespan-managed) tracks pending → active → closed transitions on candle close (`app/setups/runtime.py`). When the agent emits a `TradeIdea`, `validators.py` calls `insert_setup_from_idea` to materialize it; the runtime watches price and updates `setups` + `setup_events`.
- **F4 invalidation (migration 008)**: pending setups carry `invalidation_conditions jsonb` (a list of `RuleSpec`-shaped DSL conditions reusing `app.alerts.dsl`) and an optional wall-clock `expires_at`. The first to fire transitions `pending → cancelled` with `setup_events.event = 'invalidated'` (distinct from `'cancelled'` which is a manual user-cancel). **Naming gotcha**: migration 008 renamed `journal_trades.invalidation_px` → `stop_loss_px`; the corresponding `TradeIdea.invalidation` field became `TradeIdea.stop_loss`. The word "invalidation" is now reserved for the pre-entry concept.
- **F5 thesis persistence (migration 010)**: `journal_trades` now stores the full `summary_es_full` (verbatim copy, ≤1100 chars), plus `confluences` and `scenarios` as JSONB. This lets the review agent judge "does the thesis still hold?" without re-deriving via tools. `summary_text` (300-char truncation) stays for listings.

## Environment variables

`.env.example` is the canonical list. Most important:
- `DATABASE_URL` — accepts `postgres://`, `postgresql://`, or `postgresql+asyncpg://` (auto-promoted).
- `WATCH_SYMBOLS` — CSV. Must be kept in sync with `apps/web/lib/store/active-symbol.ts::WATCH_SYMBOLS` or symbols silently return empty OHLCV.
- `OPENROUTER_API_KEY` — required for chat (`/health` reports `openrouter: missing` when absent).
- `VOYAGE_API_KEY` — required for embeddings.
- `BETTER_AUTH_SECRET`, `BETTER_AUTH_URL`, `GOOGLE_CLIENT_ID/SECRET` — used by Next.js. Google OAuth auto-disables when client id/secret missing; email+password always works.
- `REVIEW_*` — tune the review agent: `REVIEW_CONCURRENCY` (global semaphore, default 2), `REVIEW_MAX_REVIEWS_PER_SETUP` (default 12), `REVIEW_COOLDOWN_MIN_MINUTES`, `REVIEW_TIME_OFFSETS_H` (CSV, hours after entry to schedule reviews), `REVIEW_PRICE_MOVE_PCT`, `REVIEW_APPROACHING_SL_PCT`, and `REVIEW_PRICE_*` (per-million token pricing for cost telemetry).

## Things that look unusual but are intentional

- `ruff` ignores `E741` (`l` is canonical for "low" in OHLCV across the entire crypto API ecosystem) and `RUF002/003` (Greek letters and math operators in indicator docstrings are deliberate).
- `mypy strict = true` but `disallow_untyped_decorators = false` because of FastAPI decorators, and several third-party modules (ccxt, skfolio, asyncpg) have explicit `ignore_missing_imports` overrides — see `pyproject.toml`.
- The agent does NOT have `place_order` and will not for v1. The product is an advisor; live execution comes only after paper-trading is verified.
- The frontend uses ai-elements (a separate component family from shadcn/ui) for chat UI primitives in `components/ai-elements/`. Both coexist; ai-elements is the right choice for anything chat-shaped.
