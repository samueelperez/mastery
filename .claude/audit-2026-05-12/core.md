# Audit: core/

**Auditor**: general-purpose agent
**Fecha**: 2026-05-12
**Módulo**: apps/api/app/core/

Subdirs auditados: `auth/`, `broadcasting/`, `exchanges/`, `observability/`, `config.py`, `db.py`. Refactor PR1–PR11 ya consolidado; los 87 imports `from app.core` en `app/` y los 6 en `tests/` resuelven todos (verificado con `python -c "import …"`). El módulo es la pieza de infra más compartida del backend (todos los runtimes, dispatchers, rutas y tools dependen de él), pero también la **menos testeada**: cero unit tests para `auth/`, `db.py`, `config.py`, y `broadcasting/pubsub.py`.

---

## 🔴 Critical

### C1. WebSocket auth filtra el session token al log de proxies/CDN
- `apps/api/app/market/ws_routes.py:36-53` (`_ws_user_id`): el cliente pasa el token como **query param** (`?token=…`) porque los browsers no envían `Authorization` headers en WS. Las URLs de WS son típicamente logueadas en access logs de Railway, Vercel, Cloudflare, y cualquier reverse-proxy intermedio.
- Combinado con `apps/api/app/core/auth/session.py:96-118` que loguea `token_prefix=token[:8]`, hay dos rutas distintas por las que el token (o un prefijo identificable) puede acabar en sistemas de observability de terceros.
- **Impacto**: una sesión BetterAuth es portadora — `lookup_user_id_for_token` solo comprueba `expiresAt > now()` y no re-verifica HMAC, así que cualquiera con el token raw puede hacerse pasar por el user hasta la expiración.
- **Mitigación**: (a) mover el token de la query string a un primer mensaje del WS (`auth` frame) antes de aceptar la conexión, o usar el `Sec-WebSocket-Protocol` subprotocol header como hace OpenAI/Anthropic; (b) bajar el `token_prefix` log a `len(token)` solamente, o subirlo a `log.debug` (`session.py:103-118`).

### C2. `extract_session_token` / `extract_bearer_token` parsean mal tokens sin HMAC
- `apps/api/app/core/auth/session.py:34-43, 46-64`: la lógica es "si hay `.` en el token, hacer `rsplit('.', 1)` y devolver la parte izquierda". El comentario dice "the token has no `.` in it" — pero **eso es una asunción sobre el formato interno de BetterAuth que no está documentada en su API pública y puede cambiar en cualquier minor release**.
- Si BetterAuth alguna vez genera tokens que contienen `.` (por ejemplo migrando a un formato tipo JWT-like), el `rsplit` cortará el token por un punto interno y todos los lookups fallarán silenciosamente → users vueltos a `/auth/login` sin error claro.
- **Mitigación**: pin la versión exacta de `better-auth` y de `bearer()` plugin en `apps/web/package.json`, y añade un test que monte un signin real contra una DB de test y verifique que `extract_session_token(token_from_cookie) == row.token`. Hoy no existe ninguno (`grep -r "extract_session_token\|extract_bearer_token" tests/ → 0 hits`).

### C3. Cero unit tests para `auth/`, `db.py`, `config.py`, `pubsub.py`
- `tests/storage/` contiene solo tests de cooldown, factor_stats, holdout, post_mortem joins — **ningún test toca `app.core.*` directamente**.
- `tests/observability/test_metrics.py` solo cubre `metrics.py`.
- Edge cases no cubiertos en absoluto:
  - **`extract_session_token`**: cookie vacía, cookie con doble `.`, cookie URL-encoded con `%2E`, token vacío resultante (`token or None` → `None` ok pero sin test).
  - **`lookup_user_id_for_token`**: token expirado (`expiresAt < now()`), token inexistente, user_id null (no debería pasar pero SQL no lo prohíbe), múltiples rows con mismo token (improbable por UNIQUE pero no asertado).
  - **`Settings._ensure_asyncpg_driver`**: comprobado manualmente en este audit, funciona, pero no hay test pinned. Cualquier refactor futuro puede romper Railway/Heroku silenciosamente.
  - **`session_scope`**: que rollback ocurre en excepción, que el commit ocurre en éxito, que dos `async with session_scope()` concurrentes no se corrompen.
  - **`extract_bearer_token`**: header con whitespace raro, casing (`bearer`/`BEARER`/`Bearer`), schema distinto (`Basic …`).

Para un módulo de auth + DB, la falta de cobertura es **el riesgo más alto del audit** — es donde una regresión silenciosa daña al user invisible y duradera.

---

## 🟡 Important

### I1. `Settings(extra="ignore")` enmascara typos en env vars
- `apps/api/app/core/config.py:11`. Si alguien escribe `WACH_SYMBOLS=…` en `.env`, pydantic-settings lo ignora silenciosamente y el watchlist queda en el default `BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT`. Para los flags `REVIEW_*`, `RISK_*`, `POST_MORTEM_ENABLED`, etc., un typo significa "el feature flag default queda activo" sin warning.
- **Mitigación**: `extra="forbid"` en non-prod (controlado por `APP_ENV`), o un test que liste las env vars declaradas en `Settings` y las compare con `.env.example` para detectar drift.

### I2. `init_engine` no es thread-safe (race condition de inicialización)
- `apps/api/app/core/db.py:17-29`: `if _engine is None: _engine = create_async_engine(...)`. En FastAPI con uvicorn `--workers > 1` el patrón es fork → cada proceso tiene su propio `_engine`, ok. Pero **si una request entra antes de que termine `lifespan` `init_engine()`** (cosa que sí puede pasar bajo carga / cold-start en Railway), dos coroutines pueden ver `_engine is None` simultáneamente y crear dos engines. El segundo queda huérfano (sin dispose) → connection pool leak.
- En la práctica el bug es raro porque `init_engine()` es síncrono y rápido. Pero si añades fetch dinámico de DSN o cualquier `await` dentro, el race se materializa.
- **Mitigación**: usar un `asyncio.Lock` o, más simple, llamar `init_engine()` *solo* desde `lifespan` (no desde `session_scope`/`session_dependency` como fallback). Hoy esos dos hacen `init_engine()` defensivamente (líneas 43, 56) — útil para scripts CLI (`backfill.py`), peligroso para concurrencia.

### I3. Pool sizes son magic numbers sin presupuesto
- `apps/api/app/core/db.py:23-25`: `pool_size=10, max_overflow=5` → hard-cap 15 conexiones simultáneas. No hay `pool_recycle` (default infinito en SQLAlchemy 2 con asyncpg). Si Postgres cierra conexiones idle (Neon/Supabase free tier las cierran a los 5 min), las conexiones quedan en el pool stale; `pool_pre_ping=True` salva la mayoría pero añade RTT a cada checkout.
- Con `LiveIngestion` + `AlertsRuntime` + `SetupRuntime` corriendo + `WATCH_SYMBOLS` con 4 símbolos y N usuarios chat-eando, 15 conexiones pueden saturarse en burst (entrydetect + multi-timeframe panel build + WS clientes que abren `session_scope`).
- **Mitigación**: exponer `pool_size`, `max_overflow`, `pool_recycle` como `Settings.db_pool_*` con defaults conservadores; añadir un Gauge `mt_db_pool_in_use` (Prometheus) para detectar saturación en prod antes de que falle.

### I4. `pubsub.subscribe` no se recupera de desconexiones de Valkey
- `apps/api/app/core/broadcasting/pubsub.py:58-76`: si Valkey reinicia o pierde la conexión TCP a mitad de un `async for msg in ps.listen()`, redis-py lanza `ConnectionError`. El context manager hace `unsubscribe + aclose` y propaga la excepción al caller — pero `LiveIngestion`/`AlertsRuntime`/`SetupRuntime`/`ws_routes` necesitan reconectar.
- Hoy la responsabilidad de retry queda en el caller (cada runtime tiene su propio loop), pero no hay un wrapper estándar `subscribe_with_retry` ni un test de "Valkey muere mid-stream".
- **Mitigación**: añadir un `async def subscribe_resilient(channel, *, backoff=…)` que envuelva `subscribe()` con backoff exponencial + jitter, y migrar los 4 callers para usarlo. Hoy si Valkey rebota, los WS clientes se desconectan y la siguiente vela tarda en propagarse.

### I5. `valkey_ping` swallow-all `Exception` es muy ruidoso de diagnosticar
- `apps/api/app/core/broadcasting/pubsub.py:79-88`: `except Exception: return False`. Si Valkey está caído, `/health` devuelve `valkey: "fail"` pero no se logue **por qué** (DNS, auth, timeout). En Railway con Valkey en otro service, el modo más común de fallo es DNS o el firewall, y los signos son indistinguibles sin logs.
- **Mitigación**: `log.warning("valkey.ping_failed", error=str(exc))` antes del `return False`.

### I6. `Settings.cors_origins` default incluye localhost en prod
- `apps/api/app/core/config.py:48-51`: default `http://localhost:3000,http://localhost:3001`. Si alguien deploya a Railway sin setear `CORS_ORIGINS`, la API acepta requests **solo** de localhost — eso no es vulnerable (CORS rechaza Vercel), es un bug funcional, pero es un footgun común. Más serio: no hay validación de que las origins sean URLs válidas.
- **Mitigación**: en startup, si `cors_origin_list` contiene `localhost` y no hay un origin de prod, log un warning. O mejor: validador pydantic que rechace defaults en prod (heurística: si `DATABASE_URL` no es localhost → CORS no debería incluir localhost).

---

## 🟢 Minor / cleanup

### M1. `extract_bearer_token` no normaliza casing del prefix con `strip`
- `apps/api/app/core/auth/session.py:55-56`: `parts[0].lower() != "bearer"`. Si llega `Authorization: Bearer\t<token>` con tab en lugar de espacio, `split(" ", 1)` produce `["Bearer\t<token>"]` y se rechaza. Edge case probablemente nunca visto en producción, pero el spec HTTP permite cualquier LWSP-char.
- **Fix**: `authorization.strip().split(None, 1)`.

### M2. Settings instance no cachea `cors_origin_list` / `watch_symbol_list`
- `apps/api/app/core/config.py:202-207`: cada vez que se accede a la property, se reparten + strip-ean. En el critical path no se llama frecuentemente, pero el cost es trivial de evitar con un `@cached_property`.

### M3. `_to_spot_symbol` es privado pero importado por tests
- `apps/api/app/core/exchanges/spot_adapter.py:28` (`_to_spot_symbol`) es importado por `tests/agent/test_basis.py:22`. Si quieres respetar la convención del underscore-prefix, expónelo como `to_spot_symbol` (o haz una factory `_to_spot_symbol → spot_symbol_from_perp`). No es bug, es smell.

### M4. `pubsub._client` y `db._engine` son singletons globales con estado mutable
- Patrón clásico de FastAPI, ok funcionalmente, pero hace los tests más frágiles (un test que llama `close_client()` afecta a tests posteriores). El patrón "lifespan owns the singleton" es razonable; documentar que los tests deben usar fixtures dedicadas (no existe ningún `conftest.py` de top-level con `dispose_engine()` cleanup).

### M5. `binance_adapter.py` permite `api_key=""` con `MAINNET_LIVE`
- `apps/api/app/core/exchanges/binance_adapter.py:50-51`: la guarda es `api_key is None`, no `not api_key`. Si pasas `api_key=""` (caso típico cuando `os.environ["BINANCE_API_KEY"]` está unset y el código hace `.get("BINANCE_API_KEY", "")`), pasa el check y CCXT falla con un error críptico al primer call autenticado.
- **Fix**: cambiar a `if ctx.needs_api_key and not (api_key and api_secret): raise ...`. F0 solo usa `MAINNET_RO`, así que latente hasta F6, pero fácil de arreglar ahora.

### M6. `OHLCVCandle` lleva `frozen=True` pero `is_closed` se infiere por wallclock
- `apps/api/app/core/exchanges/types.py:14, 25` + `normalizer.py:65`. El cómputo `candle_end <= now` significa que **el mismo timestamp evaluado en t1 y t2 produce candles con `is_closed` distinto** (frozen en valor, no en semántica temporal). Está documentado en `types.py:9-12`, así que es intencional, pero merece un comment más fuerte en `normalizer.py` (la wallclock check es la fuente de bugs cuando el server-clock skew es real).

### M7. `metrics.py` declara métricas en el módulo (no en función)
- `apps/api/app/core/observability/metrics.py`: las métricas son objetos module-level. Si alguna vez se importa este módulo dos veces (raro, pero ocurre con reloaders/hot-reload en dev), prometheus-client lanza `ValueError: Duplicated timeseries`. La práctica recomendada es `try: register / except ValueError: registry.collectors[...]`. No bloqueante.

### M8. `db.py` no expone un `Settings.database_url` redacted-for-logs
- Si algún punto futuro hace `log.info("db.connect", url=settings.database_url)` (puede pasar para debug), la password queda en logs. Hoy no ocurre, pero defensivamente: añadir `@property def database_url_redacted(self) -> str` que sustituya la password por `***`.

---

## ✅ Lo que está bien

- **`Settings._ensure_asyncpg_driver`**: cobertura correcta de los 3 prefixes (`postgres://`, `postgresql://`, `postgresql+asyncpg://`) — verificado manualmente en este audit. La doc del field-validator explica el "por qué" (Railway/Supabase/Neon/Heroku exponen `postgresql://`).
- **`session_scope`**: contract claro (`commit en éxito, rollback en exception, raise`). `expire_on_commit=False` correcto para el patrón async (evita lazy-loading post-commit que falla en async).
- **Auth flow**:
  - Header > cookie preference (`resolve_token_from_request:67-72`) — correcto para cross-domain Vercel↔Railway.
  - El HMAC suffix se quita ANTES del lookup. La columna `session.token` es UNIQUE y solo guarda raw, así que el SQL match es exacto.
  - El gate de validez es `expiresAt > now()` en SQL (server-time clock, no client) — robusto.
- **Sin ciclos en core/**: `db → config`, `pubsub → config`, `auth/session → db`, `exchanges/* → exchanges/types|normalizer|exchange_context`. Grafo acíclico y de profundidad 2.
- **PR1 desplazamiento**: todos los callers se migraron al path nuevo (`app.core.broadcasting.pubsub` etc.) — `grep -r "from app.db\|from app.pubsub\|from app.config" app/` devuelve 0. Refactor limpio.
- **No hay duplicación**: `create_async_engine` y `async_sessionmaker` solo en `db.py` (1 sitio). Todo el resto del backend pasa por `session_scope` o `session_dependency`.
- **`exchanges/`**: la dual-context API (`MAINNET_RO` / `TESTNET` / `MAINNET_LIVE`) está bien diseñada con `needs_api_key` / `is_simulated_data` properties — futureproof para F4/F6 sin retroactive abstraction. F0 solo usa `MAINNET_RO`, así que no hay riesgo actual de leak de API keys (no se pasan).
- **`observability/metrics.py`**: cardinality discipline documentada en el header (no user_id/symbol/setup_id en labels) — exactamente lo que rompe Prometheus si te descuidas.
- **CORS_ORIGINS leído de Settings** (no hardcoded), expose_headers explícito para AI SDK v6.
- **session table revocation**: el schema (migration 004) no tiene `deletedAt` porque BetterAuth signOut **borra la row** físicamente (ON DELETE CASCADE en FK a user). El check `expiresAt > now()` + ausencia de row cubre tanto expiración como revocación. Diseño correcto.

---

## Notas adicionales

### Riesgos para F4 (paper trading)

1. **Connection pool**: F4 añade un `PaperFillRuntime` que escribirá `paper_fills` por cada cierre. Suma a los 3 runtimes ya activos + chat + WS. Recomendado: instrumentar el pool ANTES de F4 (ver I3) para detectar saturación temprano.
2. **Pubsub channels**: los canales `paper:user:{user_id}:fills` que F4 va a fan-out van por `publish_json` igual que reviews. Confirmar que `subscribe_resilient` (ver I4) está antes de que F4 dependa de eso para drift en posición.
3. **Auth WS**: F4 querrá un canal `/ws/paper` per-user. El mismo problema C1 aplicará — si el path elegido sigue siendo `?token=…`, hay que arreglarlo antes (o duplicar el footgun).

### Quick wins (1-2h cada uno)

- Test `tests/storage/test_db.py`: `session_scope` commit/rollback, `init_engine` idempotente, `DATABASE_URL` auto-promote.
- Test `tests/storage/test_auth_session.py`: `extract_session_token`/`extract_bearer_token` table-driven con casos buenos y malos.
- Cambiar `token[:8]` → solo `token_len` en logs (`session.py:115`).
- Validar `api_key` truthy (no `is None`) en `BinanceAdapter` (M5).
- Añadir `log.warning("valkey.ping_failed", error=str(exc))` (I5).
- Cambiar `extra="ignore"` → `extra="forbid"` solo en non-prod (I1).
