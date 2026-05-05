# Backend Trading Copilot — FastAPI + uv + Python 3.13
# Build context: apps/api (configurar "Root Directory" en Railway).
FROM python:3.13-slim AS base

# uv: gestor rápido + cumple con uv.lock locked deps.
RUN pip install --no-cache-dir uv

WORKDIR /app

# Layer cache: deps primero. uv sync --frozen instala exactamente lo que
# está en uv.lock, nada más nada menos. --no-dev evita pytest/ruff en prod.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Source code después (cambia con cada commit, no invalida la layer de deps).
COPY . .

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Railway expone $PORT en runtime. Default 8000 para local.
# `uv run` activa automáticamente el venv (.venv/) — equivalente a
# `source .venv/bin/activate && <cmd>`. Sin esto, alembic no está en PATH.
CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
