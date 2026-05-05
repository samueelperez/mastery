# Backend Trading Copilot — FastAPI + uv + Python 3.13
# Build context: apps/api (configurar "Root Directory" en Railway).
FROM python:3.13-slim AS base

# uv: gestor rápido + cumple con uv.lock locked deps.
RUN pip install --no-cache-dir uv

WORKDIR /app

# --- Stage 1: deps locked (alta cache hit ratio) ---
# `--no-install-project` evita que hatchling intente construir el propio
# proyecto en esta capa (necesitaría README.md y src/, que aún no están).
# Solo se instalan las dependencias del lockfile.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# --- Stage 2: source code + install del proyecto ---
COPY . .
RUN uv sync --frozen --no-dev

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Railway expone $PORT en runtime. Default 8000 para local.
# `uv run` activa automáticamente el venv (.venv/) — equivalente a
# `source .venv/bin/activate && <cmd>`. Sin esto, alembic no está en PATH.
CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
