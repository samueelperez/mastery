# Audit: platform_routes/

**Auditor**: general-purpose agent
**Fecha**: 2026-05-12
**Módulo**: apps/api/app/platform_routes/

Files audited:
- `apps/api/app/platform_routes/__init__.py` (empty, 1 line)
- `apps/api/app/platform_routes/health.py` (52 lines)
- `apps/api/app/platform_routes/metrics.py` (22 lines)

Reference points:
- `apps/api/app/main.py:22-23,75-76` (router wiring)
- `apps/api/app/core/observability/metrics.py` (metric inventory used by `/metrics`)
- `apps/api/app/core/broadcasting/pubsub.py:79-88` (`ping()` used by `/health`)
- `apps/api/app/core/db.py:40-51` (`session_scope()` used by `/health`)
- `apps/api/tests/observability/test_metrics.py` (only related test file)
- PR10 commit `8d0c214` (file moves only, no code changes)

---

## 🔴 Critical

Ninguno bloqueante. El módulo es pequeño y los endpoints son funcionales.

---

## 🟡 Important

### 1. `/health` puede colgarse indefinidamente si DB o Valkey no responden (no hay timeouts)
**File:** `apps/api/app/platform_routes/health.py:28-36`

- El `SELECT 1` corre dentro de `session_scope()` sin `asyncio.wait_for(...)`. Si el pool de asyncpg agota conexiones o el servidor no acepta nuevas, la request queda colgada en el primer `await session.execute`.
- `valkey_ping()` (`pubsub.py:79`) tampoco tiene timeout — usa `redis.from_url(...)` con defaults. Si Valkey está en una red rota, el socket puede tardar minutos en romperse.
- Razón por la que importa: liveness/readiness probes (Railway, k8s, load balancers) llaman `/health` cada N segundos. Si `/health` cuelga, la plataforma puede no detectar el fallo y no reiniciar el pod, o peor, encadenar requests bloqueadas en el event loop async.
- **Fix sugerido**: envolver cada check en `await asyncio.wait_for(..., timeout=2.0)` y reportar `fail` (no levantar 500) en `TimeoutError`. Considerar además ejecutar DB y Valkey checks en paralelo con `asyncio.gather(..., return_exceptions=True)`.

### 2. `/metrics` y `/health` son públicos — confirmar postura explícitamente
**File:** `apps/api/app/platform_routes/metrics.py:1-22`, `health.py`

- Ambos endpoints son **sin auth**. El comentario de `metrics.py:5-7` lo justifica ("metrics are non-sensitive aggregates"); `/health` también es público.
- Verificación: ningún metric label contiene PII (`user_id`, `symbol` ID, `setup_id`). Labels actuales (`reason`, `kind`, `outcome`, `action`, `symbol`/`timeframe` en `gap_fill_inserts_total`, `from_status`/`to_status`/`event`). Solo `gap_fill_inserts_total` lleva `symbol` y `timeframe`, ambos bounded por `WATCH_SYMBOLS` (cardinality OK).
- `/health` filtra: estado DB (`ok`/`fail`), Valkey, y si OpenRouter / Voyage están **configured** o **missing**. **No filtra versiones de software ni nombres de tablas.** OK.
- Riesgo residual: si en algún momento se añade un metric con label `user_id` (e.g. cost-per-user, traces de chat), explota la cardinalidad **y** expone PII. Vale la pena dejar comentario en `metrics.py:14` reforzando que el endpoint es público — el comentario lo dice de pasada ("non-sensitive aggregates") pero la regla operativa no está. Idealmente un test que assert que el body no contenga ciertas patterns (`user_id=`, `email=`).
- **Recomendación deploy-time**: el comentario menciona "prod deployments should restrict via ingress (Caddy/nginx ACL or VPN)". Verificar que el Railway/Vercel actual no expone `/metrics` al público — no hay evidencia en repo de que sí o no.

### 3. `/metrics` no garantiza que todos los counters/gauges hayan sido importados antes de scrape
**File:** `apps/api/app/platform_routes/metrics.py:22`

- `prometheus_client` registra metrics en el `REGISTRY` global solo cuando el módulo donde se declaran (`app/core/observability/metrics.py`) es importado. Hoy se importa transitivamente desde `scout_dispatcher.py`, `risk_manager.py`, `setup repo`, `ingestion_live`, `telegram` — todos cargados en `lifespan` startup vía las runtimes. Funciona en práctica.
- Pero: si un Prometheus scraper hace su primer scrape **antes** de que el lifespan startup termine (carrera teórica), faltarán metrics. Más importante: la lista de métricas declaradas incluye `agent_invocations_total{kind="chat"|"review"|"post_mortem"}` pero **solo `kind="scout"` se incrementa** (grep confirma `agent_invocations_total` no aparece en `app/agent/`, `app/reviewer/`, `app/post_mortem/`). Esos buckets están muertos.
- **Fix corto**: en `metrics.py` (el endpoint), hacer `import app.core.observability.metrics  # noqa: F401` para forzar registro al primer hit del endpoint y desacoplar el orden de import del lifespan.
- **Fix mayor (fuera de módulo, pero observable aquí)**: cablear `agent_invocations_total` en `app/agent/routes.py`, `app/reviewer/agent.py`, `app/post_mortem/...`. Es deuda del módulo de observability, no de platform_routes, pero se nota al inspeccionar este endpoint.

### 4. Falta cobertura de tests para los endpoints
**Files:** `tests/observability/test_metrics.py`, ausencia de `tests/observability/test_health.py`

- `test_metrics.py:7-13` declara explícitamente que **NO** ejercita el endpoint HTTP (`tests/integration/test_scout_smoke.py:1` tampoco lo hace según búsqueda). El test solo valida que `generate_latest()` produce ciertos nombres — no que `GET /metrics` devuelve 200 con `text/plain; version=0.0.4`.
- No existe ningún test para `/health`. Búsqueda `grep -rn "/health"` en `apps/api/tests/` → 0 matches.
- Gaps concretos:
  - `/health` con DB down debe devolver 200 + `status="degraded"`, `db="fail"` (no 500).
  - `/health` con OpenRouter key ausente debe reportar `openrouter="missing"` (mencionado en `CLAUDE.md`, sin test).
  - `/metrics` debe devolver 200, content-type correcto y al menos un metric conocido.
- Recomendación: `httpx.ASGITransport(app=app)` + `AsyncClient` para 3–4 tests rápidos. No requiere DB real si se mockean los checks.

---

## 🟢 Minor / cleanup

### M1. `__init__.py` vacío sin marker / docstring
**File:** `apps/api/app/platform_routes/__init__.py` (1 línea, vacía)

- Consistente con otros sub-paquetes del repo, pero un one-liner `"""Operational HTTP endpoints: /health, /metrics."""` ayudaría al lector.

### M2. `HealthResponse` no expone versión de build ni `now`
**File:** `apps/api/app/platform_routes/health.py:14-19`

- Probes binarias funcionan, pero un campo `version: str` (ahora hard-coded `"0.0.0"` en `main.py:63`) o `commit_sha` ayudaría en debugging de despliegues. Y `now: datetime` ayuda a detectar clock skew. No urgente; deuda menor.

### M3. `health.py:33` captura `Exception` genérica
**File:** `apps/api/app/platform_routes/health.py:33-34`

- `except Exception: db_ok = False` traga cualquier error sin loggear. Si la DB falla **silenciosamente** en producción, no hay rastro. Mínimo añadir `structlog.get_logger().warning("health.db_check_failed", error=str(exc))`.

### M4. `metrics.py` sync work en async handler — comentario es correcto pero podría medirse
**File:** `apps/api/app/platform_routes/metrics.py:19-22`

- `generate_latest()` es CPU-bound. El comentario dice "fast enough for hundreds of metrics" — el inventory actual tiene ~9 metrics, perfecto. Si el inventory crece a >500 (con `gap_fill_inserts_total{symbol,timeframe,phase}` puede dispararse), considerar `run_in_threadpool`. No urgente.

### M5. Tags inconsistentes: `meta` vs `observability`
**File:** `health.py:22` (`tags=["meta"]`) vs `metrics.py:17` (`tags=["observability"]`)

- Los dos endpoints son la misma familia operacional. Decidir uno (`observability` o `platform`) y unificar. Solo afecta a OpenAPI docs grouping.

### M6. Sin `Cache-Control: no-store` en `/health` ni `/metrics`
- Si algún CDN/proxy mete caché por defecto, devolverá health stale. `Cache-Control: no-store` explícito (como hace `agent/routes.py:87` para SSE) es defensivo. Mínimo riesgo en arquitectura actual.

### M7. `valkey_ping()` no diferencia error de timeout
**File:** `apps/api/app/core/broadcasting/pubsub.py:79-88` (consumido por health, no es del módulo pero afecta a `/health`)

- Mismo "swallow exception". Para diagnóstico de deploys remotos sería útil un structlog warning con el tipo de excepción.

---

## ✅ Lo que está bien

1. **Separación de concerns correcta tras PR10**. Antes vivía en `app/api/`; ahora `platform_routes/` agrupa solo lo "operational" (health, metrics) — `chat_router` (que también vivía en `api/`) se movió a `agent/routes.py`. Naming convención clara.
2. **PR10 fue puro `git mv`** — no riesgo de cambios de comportamiento. Confirmado en `git show 8d0c214 --stat`: solo renames.
3. **`HealthResponse` es un Pydantic model con `Literal` types** — esquema validado, no string-soup. OpenAPI lo documenta correctamente.
4. **`/health` NO hace outbound calls a OpenRouter/Voyage** (`health.py:24-27` lo explica). Correcto — esos checks costaríán $$ por probe.
5. **Cardinality discipline en `metrics`**: `core/observability/metrics.py:24-26` documenta explícitamente la regla "no user_id en labels". Cumplida en la inventario actual.
6. **Endpoint `/metrics` usa `CONTENT_TYPE_LATEST`** correcto — Prometheus scrape valida headers.
7. **Imports limpios** en ambos archivos: solo lo necesario, sin transitivos.
8. **El módulo no quedó código en sitios viejos** — `find` confirma que `app/api/` ya no existe; las únicas carpetas con esos nombres están bajo `core/` y `market/` y son legítimas (`core/broadcasting`, `core/observability`, `core/auth`, `market/indicators`, `market/dominance`).
9. **`/health` reporta `openrouter: missing` / `voyage: missing` correctamente** (líneas 38–39, 50–51), cumpliendo el contrato documentado en `CLAUDE.md`.

---

## Notas adicionales

- **No hay deuda relacionada con OpenTelemetry**: la convención del proyecto es Prometheus-only por ahora. Si en F5/F6 se introduce OTel para traces, este módulo seguramente recibirá un `/v1/traces` o similar.
- **Magic numbers**: ninguno en el módulo. Pool size (`10`) está en `core/db.py`, no acá.
- **Logging**: ambos endpoints son silentes (no logean cada hit). Correcto — son hot-path de probes; loggear cada uno spammea logs. Solo el "DB swallow exception" (M3) merece log.
- **Concurrencia `/metrics`**: el `REGISTRY` global de `prometheus_client` es thread-safe (uses `threading.Lock` internamente para `Counter.inc()`). `generate_latest()` toma snapshot atómico. OK.
- **Adjacent finding (no del módulo)**: `agent_invocations_total{kind="chat"|"review"|"post_mortem"}` está declarado pero solo `kind="scout"` se incrementa. Worth investigar antes de F4 si quieres dashboards de cost-per-kind.
- **Adjacent finding**: el test `test_metrics.py:108` llama a `generate_latest()` sin importar `metrics.py` del endpoint — no testea la ruta. Sería trivial añadir un test que use ASGITransport.
