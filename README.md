# Trading Copilot 2026

Opinionated implementation of a crypto trading copilot. The LLM is interpreter and orchestrator, never an oracle — every quantitative claim cites a deterministic tool.

See `docs/Crypto Trading Copilot 2026_ Opinionated Technical Blueprint.md` for the full design rationale, and `.claude/plans/vamos-a-hacer-un-luminous-hopper.md` for the current phase plan.

## Status

**Phase 0 (Cimientos)** — in progress. Goal: live BTCUSDT 1h chart in the browser, 2 years of 1m history backfilled to Postgres, on architecture that scales to F1+.

## Stack

- **Backend** (`apps/api`): Python 3.13, FastAPI, SQLAlchemy 2 async, alembic, ccxt, structlog. Managed by `uv`.
- **Frontend** (`apps/web`): Next.js 16 App Router, React 19, Tailwind 4, shadcn/ui (Nova preset), ai-elements (F1+), Lightweight Charts.
- **Storage**: PostgreSQL 18 + TimescaleDB + pgvector (Docker), Valkey 8 (Docker).
- **Tooling**: Turborepo, pnpm 9, OrbStack.

## Quickstart

Prerequisites: Node 22, pnpm 9, Python 3.13, [uv](https://docs.astral.sh/uv/), [OrbStack](https://orbstack.dev/) (or Docker Desktop).

```sh
# 1. Install JS workspace deps
pnpm install

# 2. Bring up the data plane (Postgres + TimescaleDB + Valkey)
docker compose up -d

# 3. Backend setup
cd apps/api
uv sync
uv run alembic upgrade head

# 4. Backfill BTCUSDT 1m (2 years, ~10–20 min)
uv run python -m app.ingestion.backfill --symbol BTCUSDT --tf 1m --years 2

# 5. Start backend (terminal A)
uv run uvicorn app.main:app --reload --port 8000

# 6. Start frontend (terminal B)
cd ../../apps/web
pnpm dev
# Open http://localhost:3000
```

## Layout

```
trading/
├── apps/
│   ├── api/          # FastAPI backend (Python, uv)
│   └── web/          # Next.js frontend (pnpm, shadcn/ui + ai-elements)
├── packages/
│   └── shared-types/ # canonical TS types (autogen from Pydantic later)
├── docs/             # blueprint and reference material
├── docker-compose.yml
├── pnpm-workspace.yaml
├── turbo.json
└── package.json      # workspace root
```
