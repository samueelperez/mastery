# trading-api

FastAPI backend for the Trading Copilot. Managed by [uv](https://docs.astral.sh/uv/).

## Setup

```sh
# Install deps + create venv
uv sync

# Apply migrations (requires docker compose up -d in the repo root first)
uv run alembic upgrade head

# Run dev server
uv run uvicorn app.main:app --reload --port 8000
```

## Layout

```
apps/api/
├── pyproject.toml
├── alembic.ini
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
└── app/
    ├── main.py            # FastAPI app + lifespan (live ingestion task lives here)
    ├── config.py          # pydantic-settings (env-driven)
    ├── db.py              # SQLAlchemy async engine
    ├── api/               # HTTP routers
    ├── data/              # exchange adapters (CCXT) and dual-context (testnet/mainnet)
    ├── ingestion/         # backfill + live WS klines
    ├── storage/           # SQLAlchemy models + repositories
    └── broadcasting/      # Valkey pub/sub fan-out to frontend WS clients
```
