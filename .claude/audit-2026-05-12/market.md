# Audit: market/

**Auditor**: general-purpose agent
**Fecha**: 2026-05-12
**Módulo**: apps/api/app/market/

Alcance: `dominance/`, `indicators/` (core, trend, momentum, volume, panel), `ohlcv/` (backfill, ingestion_live, repo, routes, models), `ws_routes.py`.

---

## 🔴 Critical

### 1. `tests/indicators/test_panel.py` está roto — referencia `app.indicators.panel` (path pre-refactor)
- `tests/indicators/test_panel.py:41,103,131` monkeypatchean `"app.indicators.panel.fetch_range"`, módulo que **ya no existe** post-PR.
- `uv run pytest tests/indicators/` → **3 failed, 52 passed** con `ImportError: No module named 'app.indicators'`.
- Las 3 pruebas que cubren `compute_panel` (single-pass para `tools/indicators.py` y `panel_service`) están silenciosamente OFF. Esto incluye el test de regresión B4 (`bbands(20) + bbands(50)` sin colisión de columnas).
- Fix: `app.indicators.panel.fetch_range` → `app.market.indicators.panel.fetch_range`.

### 2. `/ws/market` **no exige autenticación** — fuga de datos potencial
- `app/market/ws_routes.py:56-91` acepta cualquier WebSocket sin extraer `user_id`. `/ws/alerts` y `/ws/reviews` sí pasan por `_ws_user_id`.
- OHLCV es global (no per-user) y la watchlist es pública, pero:
  - sin auth, un atacante externo (CSWSH desde otro origen, sin SameSite check en WS) se cuelga al pub/sub ilimitadamente — DoS trivial (un cliente por (symbol, tf) sin rate-limit).
  - El docstring del módulo (línea 9-11) dice "scoped to the authenticated user … from the BetterAuth session cookie" — **mentira** para `/ws/market`. La inconsistencia entre comentario e implementación es la clase de bug que se vuelve un CVE.
- Mínimo: aplicar `_ws_user_id` también a `/ws/market`. Cuando F4 se publique con paper-trading per-user, esto ya queda blindado.

### 3. Layering violation: `market/` importa de `agent/`
- `app/market/ohlcv/repo.py:15` y `app/market/ohlcv/ingestion_live.py:19` hacen `from app.agent.tools._time import floor_to_timeframe`.
- `market/` es capa de datos; `agent/` la consume. Esto invierte la dirección y crea ciclo conceptual (el día que muevas/borres una tool, te rompe el data plane).
- Fix: mover `floor_to_timeframe` (y `staleness_warning` si conviene) a `app/core/time.py` o `app/market/_time.py`. Es función pura sin deps al agente.

### 4. `ws_routes.py` cuelga si el cliente deja de leer (no usa `wait_for` sobre `send_json`)
- En `/ws/market` (líneas 71-81), si el cliente se desconecta sucio o consume lento, `pubsub.get_message` sigue trayendo mensajes y `send_json` se bloquea. La rama `WebSocketDisconnect` solo dispara cuando el peer cierra limpio; con un cliente "muerto pero TCP-vivo" la corrutina nunca progresa y el slot de pubsub queda colgado.
- Falta backpressure: o un `asyncio.wait_for(send_json, timeout=X)`, o un buffer + drop-policy. En producción multi-tenant esto se nota.

---

## 🟡 Important

### 5. Ingestion live: gap-fill **no idempotente bajo concurrencia con `/watch_loop`**
- `app/market/ohlcv/ingestion_live.py:77-80`: cada reconexión llama `_fill_gap(phase="reconnect")` antes de re-suscribir al WS. El comentario (l. 75) dice "bulk_upsert es idempotente, así que solapar … es safe" — cierto a nivel SQL (ON CONFLICT DO NOTHING).
- Pero: nada impide **dos `start()`** simultáneos del mismo `LiveIngestion` desde fuera (tests, reload de uvicorn, hot-reload). El check `if self._adapter is not None: return` (l. 293) protege re-entries del mismo objeto pero no instancias separadas. Y `_fill_gap` interno no toma lock por `(symbol, tf)` — el `_watch_loop` y el `_fill_gap` de la siguiente reconexión podrían disparar la misma página REST en paralelo (gasta cuota, no rompe correctness).
- Fix: un `asyncio.Lock` por `(symbol, tf)` compartido entre `start()` y `_watch_loop._fill_gap`.

### 6. `_watch_loop` reconnect backoff: 2.0s fijo, **sin jitter, sin cap exponencial**
- `ingestion_live.py:117` `await asyncio.sleep(2.0)`. Magic number, sin reasoning ni env.
- Si Binance tira un 1006 por mantenimiento (10-15 min), reintentamos cada 2s indefinidamente → ~300 intentos × 4 símbolos × 5 TFs = 6000 reconnects que Binance puede rate-limitar (banhammer IP).
- Fix: exponential backoff con jitter (`min(max_backoff, base * 2**attempts) + random.uniform(0, jitter)`) y reset a base cuando `watch_ohlcv` haya devuelto al menos un candle.

### 7. `fetch_range` no usa el índice óptimo cuando `since=None`
- `ohlcv/repo.py:97-113`: `order_by(ts.desc()).limit(N)` con filtro WHERE `ts < effective_until`. Bien para query "últimas N".
- Pero la migración crea índice `ohlcv_symbol_tf_ts_desc` sobre `(symbol, timeframe, ts DESC)` — **falta `exchange`** como primer key. Con un solo exchange (binance_usdm) hoy no importa, pero el modelo es multi-exchange "from day one" (migración 001 l. 43). En cuanto entre OKX, el índice ya no segrega.
- Fix: o renombrar índice a `(exchange, symbol, timeframe, ts DESC)`, o documentar que se hardcodeará `exchange='binance_usdm'`. Si lo segundo, eliminar la columna y simplificar.

### 8. `existing_ts_in_window` returns `set[datetime]` — puede ser grande sin paginación
- `ohlcv/repo.py:158-184` carga **todos** los ts en `[since, until)`. Con `LOOKBACK_GAP_SCAN_CANDLES=500` × 5 TFs × 4 symbols × reconnect frequency, ~10k rows in memory periódicamente. Aceptable hoy. Si subes `LOOKBACK_GAP_SCAN_CANDLES` o añades 20 símbolos, el set crece. Considera paginación o un `EXCEPT` SQL contra una CTE generadora.

### 9. `compute_panel` carga TODO en Python list-of-dicts (l. 155-163) — costoso para lookback grande
- Para `lookback=1000` × 4 símbolos × 5 TFs = 20k filas con Python attribute access (`r.ts`, `r.o`, ...) por col. Polars haría mejor con `pl.from_pandas` o `pl.DataFrame.from_records`.
- Bench-able; pero el path está caliente (cada tool call del agente lo dispara).

### 10. `OHLCV.l` columna usa "l" — ya documentado, pero ruff regla local
- `ruff` ignora `E741` globally (en pyproject); confirma intent. Sin embargo, en `models.py:21` y `routes.py:29`/`63` el uso de `l` es consistente. OK.

### 11. Frontend `WATCH_SYMBOLS` y backend `WATCH_SYMBOLS` están **sincronizados manualmente**
- `apps/web/lib/store/active-symbol.ts:10-15`: lista hardcoded `BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT`.
- `.env.example`: `WATCH_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT` (coinciden hoy).
- No hay test ni health-endpoint que valide la sincronía. Cualquier cambio en backend sin tocar frontend produce el bug silente que el comentario del frontend advierte. **F4 (paper trading) ampliará la watchlist** → momento perfecto para resolverlo:
  - Opción A: backend expone `GET /watchlist` y el frontend lee al boot.
  - Opción B: build-time codegen desde un yaml compartido.

### 12. Dominance: no hay `pubsub_channel` ni test backend
- `dominance/provider.py` tiene `parse_coingecko_global`, `classify_trend`, `classify_regime` — pure funcs sin tests dedicados en `tests/`. Solo `tests/agent/test_dominance_provider.py` (en el otro lado del refactor); falta cobertura unitaria de `classify_regime` thresholds (53/47, "flat" semantics).
- Funcionalmente sano, pero la lógica con thresholds mágicos (53.0, 47.0, flat_threshold=0.5) merece tests de matriz: cada regime label × cada combinación de trend ∈ {up, down, flat, indeterminate}.

### 13. `_fill_gap`: la rama "serie vacía" no usa `_group_consecutive` — busca todo el rango aunque tenga overlap con backfill CLI
- `ingestion_live.py:174-210` (rama "empty series") siempre pide `INITIAL_SEED_CANDLES=1000` velas desde Binance. Si el backfill CLI ya rellenó hasta 6 meses atrás pero por alguna razón `last_ts` devuelve None (race, primera arranque tras crash mid-write), refetcheamos las 1000 últimas y dependemos de `ON CONFLICT DO NOTHING`. Es safe pero gasta cuota.
- Fix: la rama "empty series" podría delegar al mismo flujo "non-empty" usando `window_start = floor_now - INITIAL_SEED_CANDLES * delta`. Unificaría code paths.

### 14. `vwap(anchor="session")` resetea por `dt.truncate("1d")` en UTC — **no exchange session**
- `volume.py:38-43`. Comentario lo aclara: "00:00 UTC". Para futuros perp esto es razonable; para spot equities sería incorrecto. Documentado, OK — pero anota en CLAUDE.md o tool docs que VWAP "session" significa "día UTC", no "sesión Asia/EU/US".

---

## 🟢 Minor / cleanup

### 15. Docstrings stale post-refactor
- `app/market/indicators/__init__.py:4` "`app.data.types.OHLCVCandle`" → es `app.core.exchanges.types.OHLCVCandle`.
- `app/market/indicators/__init__.py:9` "`app.storage.ohlcv_repo.fetch_range`" → es `app.market.ohlcv.repo.fetch_range`.
- `app/market/ohlcv/backfill.py:4` ejemplo CLI dice `python -m app.ingestion.backfill` → es `python -m app.market.ohlcv.backfill` (CLAUDE.md ya tiene el path correcto, pero el docstring del archivo no).
- `app/alerts/dsl.py:3` "mirrors `app.indicators.IndicatorSpec`" → `app.market.indicators.IndicatorSpec`.

### 16. `app/market/__init__.py` y `app/market/ohlcv/__init__.py` y `app/market/dominance/__init__.py` están **vacíos**
- Es una elección, pero los demás paquetes nuevos (`indicators/__init__.py`) re-exportan públicos. Inconsistencia menor; o todos exportan o documentas la convención.

### 17. `repo.py::fetch_range` filtra `OHLCV.ts < effective_until` (strictly less) pero `OHLCV.ts >= since` — asimétrico
- `repo.py:103,109`. Funcionalmente coherente con la doc (closed-candle exclusion del bucket en curso), pero la asimetría no se documenta en la signature. Añade nota: "since inclusive, until exclusive".

### 18. `count_rows` importa `func` localmente (l. 123)
- En `last_ts` también (l. 145). Patrón unificable arriba del file — cosmético.

### 19. `_DEFAULT_LENGTHS` en `panel.py:38-47` incluye `macd:0` y `vwap:0` con un comment "ignored" — magic
- Refactor: `dict[IndicatorName, int | None]` con `None` para macd/vwap, y el `match` ya no necesita el "ignored" sentinel.

### 20. `routes.py::get_ohlcv` no valida `timeframe` contra la lista soportada
- `app/market/ohlcv/routes.py:43`. Un cliente puede pedir `tf="2y"` y obtener `[]`. Mejor: `Query(regex="^(1m|15m|1h|4h|1d)$")` o convertir el path param a `Literal`. Mismo para `symbol` (whitelist `WATCH_SYMBOLS` o al menos formato).

### 21. `MACD` no expone los parámetros `fast/slow/signal` por `IndicatorSpec`
- `panel.py:102` invoca `macd(lf, source=spec.source)` con defaults fijos 12/26/9. Si en el futuro algún tool quiere MACD 5/35/5 (Elder, conjugate), hay que ampliar `IndicatorSpec` con un campo `params: dict | None` o flags específicos. No urgente.

### 22. `bbands.bb_bw = (upper - lower) / mid` puede ser `null` cuando `mid is null` (warmup) — OK
- pero no protege div-by-zero si `mid == 0` en algún edge case sintético (closes negativos en tests). En prod (precios > 0) imposible. Cosmético.

---

## ✅ Lo que está bien

- **Indicators no lookahead**: `core.py`, `momentum.py`, `trend.py`, `volume.py` usan `adjust=False` consistentemente y `min_samples=length` para no producir valores en el warmup. Tests cubren el invariante en `tests/indicators/test_no_lookahead.py` para los 8 indicadores principales (52 tests passing).
- **Wilder smoothing** explícito (`alpha=1/length`) en RSI/ATR/ADX (`core.py:65,101`, `trend.py:41-44`) — comentario didáctico in-place.
- **RSI robusto**: `100 * avg_gain / (avg_gain + avg_loss)` evita div-by-0 en bombeos puros, y el null en flat-series está documentado y testeado (`test_core.py::test_rsi_pure_pump_returns_100`, `test_rsi_flat_series_returns_null_post_warmup`).
- **ADX**: maneja el warmup correctamente (`atr_w.is_null() → None`) y el caso plano post-warmup (`atr_w == 0 → 0`, no `inf`).
- **`fetch_range` clamp defensivo a `floor_to_timeframe(now, tf)`** (repo.py:94) — invariante "solo cerradas" enforced en el query, no asumido.
- **`upsert_one` returns bool** indicando newly inserted; usado en `_watch_loop` para deduplicar publishes.
- **Gap-fill robusto**: tres escenarios (empty seed / tail / mid-history) cubiertos. El `_group_consecutive` (l. 120) trocea los missing en clusters para minimizar requests REST.
- **Hypertable opcional**: la migración 001 detecta `pg_available_extensions` antes de `CREATE EXTENSION timescaledb` para no contaminar la transacción si no está disponible. Patrón correcto para multi-provider (Railway/Neon/RDS).
- **Pubsub fan-out** entre live ingestion y WS clients: clean separation, idempotent channel naming (`market_channel`), JSON payload incluye `is_closed`.
- **Dominance**: la arquitectura cache → live fetch → history (sorted set en Valkey) está bien pensada. `_load_history_near` con ventana de tiempo es el approach correcto.
- **`is_closed` inference** desde wall-clock está documentada (`exchanges/normalizer.py:42-47`, `types.py:9-25`) — no es lo ideal pero es lo que CCXT permite.
- **Indicador panel** modular: un único `compute_panel` que chainea N specs en una sola `collect()` → costoso ya optimizado para frecuente.

---

## Notas adicionales

- **Test coverage gap crítico**: cero tests para `ingestion_live.py` (LiveIngestion start/stop, `_fill_gap`, `_watch_loop`, reconnect, `_group_consecutive`). El componente más delicado del módulo no tiene mock-WS test. `tests/storage/` y `tests/runtime/` no lo cubren. Para F4 (paper-trading depende de live OHLCV), bloquearlo merece 3-5 tests con CCXT mockeado.
- **Sin test del WebSocket handler** (`ws_routes.py`). Misma razón: pre-F4 conviene asegurar cancel propagation y reconnect del cliente.
- **No hay tests de `routes.py::get_ohlcv`** (e2e HTTP). Riesgo bajo por simplicidad, pero la validación de `timeframe` y `symbol` (issue #20) sería el momento.
- **Métrica `gap_fill_inserts_total` está bien instrumentada** (`ingestion_live.py:207,276`) — diferencia `phase=startup` vs `phase=reconnect` para detectar WS inestable.
- **CLAUDE.md "Data plane" sección coherente** con el código auditado: TimescaleDB + WATCH_SYMBOLS sync + Valkey pub/sub + `session_scope()` patron — todo respetado.
- **Próximo paso recomendado antes de F4**: arreglar #1 (tests rotos) y #2 (WS auth) — son baratos y el resto puede entrar en backlog F4.5.
