# Audit: alerts/

**Auditor**: general-purpose agent
**Fecha**: 2026-05-12
**Módulo**: apps/api/app/alerts/

## 🔴 Critical (bloqueante para F4)

- [`runtime.py:184-209`] `_evaluate_close` mantiene `session_scope()` **abierto durante el `await compute_panel_for_specs(...)`** (línea 200, dentro del `async with session`). El panel compute hace I/O contra Timescale; mientras tanto la transacción enmarcada por `session_scope` permanece abierta y la lectura final `session.commit()` se ejecuta al salir. En F4 (paper trading 24/7) con múltiples loops por símbolo+TF más SetupRuntime usando el mismo helper, esto bloquea conexiones del pool más tiempo del necesario y, si dos loops contienden el mismo panel, multiplica la presión sobre el pool de asyncpg. **Por qué bloquea**: ya hay reportes de Timescale connection-pool exhaustion en runtimes 24/7; mantener sesión abierta durante fetch es un patrón conocido que se rompe en producción cuando la carga sube.

- [`runtime.py:345-381`] `_record_bias_promoted` recibe el JSON crudo de `pg_notify` (proveniente del trigger `notify_bias_high` en migración 003) y lo inserta como `snapshot` directamente: `{"snap": bias_payload_json}` con `CAST(:snap AS jsonb)`. **No se valida el shape**. Si alguna vez se insertara un `bias_events` con un payload corrupto/inesperado (o si la migración 003 cambiase el `json_build_object`), el INSERT graba basura sin esquema. Además, `user_id = bias_event.get("user_id", "me")` cae al default legacy `'me'` si el campo falta — en F4 multi-user esto enviaría una alerta a un usuario equivocado o "global". **Por qué bloquea**: en multi-user el fallback `'me'` puede leakear eventos de bias a un user_id legado o crear filas huérfanas; el alert_events tiene `user_id text NOT NULL` pero acepta cualquier string.

- [`runtime.py:309-342`] `_bias_listener_loop` **es un único listener Postgres global** (no por-user). Si la API corre con `>1 worker` (uvicorn workers >1, o Railway con réplicas), **cada worker se suscribe al mismo `bias_events_high`** y cada evento se promueve N veces → N inserts de `alert_events` con `kind='bias_promoted'` y N publishes por evento de bias. No hay claim atómico tipo `claim_review_slot`. **Por qué bloquea**: en cuanto se escale horizontalmente (F4+ con paper-trading 24/7), el feed de alertas duplicará eventos por número de réplicas.

## 🟡 Important (atender pronto, no bloqueante)

- [`runtime.py:184-273`] `_evaluate_close` abre **dos** `session_scope()` separados (lectura de reglas + panel; luego escritura de hits). Entre ambos puede ocurrir que otro worker (o el endpoint REST) mute la regla (PATCH disabled / cooldown). La lógica de cooldown (`_is_within_cooldown`) usa `last_fired_at` del primer scope, así que dos closes simultáneos del mismo TF pueden ambos pasar la check y disparar el hit dos veces. El UPDATE en `_record_hits_batch` no es condicional sobre `last_fired_at < now() - cooldown_s`. **Por qué importa**: es el mismo race pattern que motivó `claim_review_slot` en el reviewer; debería resolverse con un UPDATE condicional que devuelva rowcount=0 cuando otro hilo ya disparó.

- [`runtime.py:217`] `_is_within_cooldown` lee `last_fired_at` en Python con `datetime.now(tz=UTC) - last`. Si `last_fired_at` viene de Postgres SIN timezone (raro pero posible si alguna escritura usase `now()::timestamp`), la resta lanzaría `TypeError`. La columna está declarada `timestamptz` en la migración 003 — vale, pero no hay defensa: una task que crashee aquí no se reintenta dentro de la misma evaluación (se loguea como `alerts.evaluate_close.error`).

- [`runtime.py:112-127`] `_SPEC_CACHE` es **global del módulo y no thread-safe**. En el modelo asyncio actual con un solo event loop esto es OK, pero la entrada nunca se purga: si un rule_id se borra hard (hoy es soft-delete pero la regla pasa a `enabled=false` y nunca vuelve a `_fetch_active_rules`), la entry queda en memoria para siempre. No es un leak grave (un dict crece despacio), pero en 24/7 con miles de rules ediciones acumulan basura.

- [`runtime.py:255-273`] Tareas de `dispatch_scout_match` se crean con `asyncio.create_task` y se guardan en `_SCOUT_TASKS`. En `AlertsRuntime.stop()` **no se cancelan ni se esperan** — solo se cancelan las del propio `self._tasks`. Si shutdown ocurre con scouts en vuelo, esas corrutinas se pierden silenciosamente cuando el event loop se cierra (logs como "Task was destroyed but it is pending!"). **Por qué importa**: pierde proposals en flight durante deploys; debería await + cancel con timeout en `stop()`.

- [`runtime.py:208-218`] La verificación `panel.height == 0` ocurre **fuera** del primer `session_scope`. Si el panel está vacío (símbolo recién añadido a WATCH_SYMBOLS sin OHLCV), la función retorna pero **no logea** que la regla se evaluó contra un panel vacío. En F4 puede dar la falsa impresión de que el runtime está silencioso "porque no hay matches" cuando en realidad nunca tuvo datos.

- [`runtime.py:97-105`] `_is_within_cooldown` usa `cooldown_s=0` como "sin cooldown" (early return). Pero el INSERT inicial en `routes.py` y `create_alert.py` permite `ge=0`, lo que se documenta como "minimum gap between fires (default 1h)" — `cooldown_s=0` deja al rule disparar en cada candle close. No hay test que valide explícitamente este edge case (¿debería ser disparable o ser tratado como always-cooldown?). Decisión silenciosa.

- [`runtime.py:140-167`] `_record_hits_batch` hace N INSERTs + N UPDATEs en bucle, **no batched**. Para reglas que disparen masivamente (rare pero posible si hay ~10 reglas para un mismo símbolo/tf y todas matchean) genera 2N round-trips. No es bloqueante hoy con pocos rules, pero es un hot-path en F4.

- [`runtime.py:393-405`] `AlertsRuntime.start()` crea **una task por (símbolo, tf)** y una de bias. `get_watch_list()` se llama una sola vez al startup — añadir un símbolo a `WATCH_SYMBOLS` requiere restart (documentado en `ingestion_live.py:40`, pero **no** en `runtime.py`; un dev que toca alerts no sabrá esto).

- [`cooldown.py:79-86`] `evaluate_streak` con `r_mult is None` lo trata como pérdida (cuenta hacia streak), pero `_fetch_recent_closures` lo convierte a `0.0` antes de retornarlo (línea 193). Con `r_value=0.0`, la condición `r_mult is None or r_mult <= 0` se evalúa como True por la cláusula `<=0`. **Funciona por casualidad**; un cambio en `_fetch_recent_closures` que mantenga `None` rompería las asunciones porque el campo viene como `float` en la tupla. Inconsistente con el docstring de la línea 170-171 ("Closures with `r_multiple IS NULL` are still returned and counted as losses").

- [`dsl.py:65-69`] `_normalize_symbol` muta `self` vía `object.__setattr__` post-init. Funciona pero es frágil — `model_dump()` retornará el símbolo en upper y `model_validate(roundtrip)` lo normaliza de nuevo, pero si pydantic-AI usa el spec literal del LLM (en el JSON tool args) antes del validator, podría persistir lower-case. **Sugerido**: usar un `field_validator` que devuelva el upper, no `model_validator(mode='after')` con setattr.

- [`evaluator.py:96-128`] `build_snapshot` re-evalúa cada `c` para construir `matched_conditions` — duplica el trabajo ya hecho en `evaluate_rule`. Edge case: si las condiciones tienen `logic='any'` y solo una matched, `matched_conditions` lista correctamente la única que disparó, pero si todas fallan y `evaluate_rule` retornó False (por la cláusula `if logic=='all'`), igual se llama a `build_snapshot` en runtime — espera, **no**: `runtime.py:219-220` solo llama a `build_snapshot` cuando `evaluate_rule` es True. OK, no bug, pero el doble cómputo es ineficiente.

## 🟢 Minor / cleanup

- [`__init__.py:14`] El docstring referencia `app.api.alerts` — path que **no existe** en el repo (es `app.alerts.routes`). Stale doc post-refactor PR8-11.

- [`panel_service.py:26-32`] El docstring dice `× 3` pero **CLAUDE.md** (sección Alerts F3) dice "Wilder smoothing × 2 + cross headroom, floored at 60". El código ejecuta `× 3`. **Doc drift**: o se actualiza CLAUDE.md o el código. Esto es exactamente la advertencia del propio CLAUDE.md ("the easy thing to break"). La heurística actual es más conservadora (más candles) — probablemente intencional, pero la doc miente.

- [`panel_service.py:32`] Magic number `60` floor sin constante nombrada. Hace tests más frágiles si se tunea.

- [`runtime.py:284`] `timeout=30.0` en `pubsub.get_message` es magic. Si Valkey muere y resucita >30s después, el loop entra al except y reconecta — ok, pero el número merece nombre + comentario.

- [`runtime.py:306`] `await asyncio.sleep(2.0)` tras error en market loop, igual en bias listener (línea 342). Backoff exponencial sería mejor para evitar spam en errores persistentes (Valkey/PG down extendido).

- [`cooldown.py:81-83`] `# We can early-exit but we want the FULL count for telemetry.` comentario contradictorio: `continue` siempre incrementa, no hay early-exit visible. El `continue` no agrega valor (caería al siguiente if/else en la siguiente iteración igual).

- [`dsl.py:72-75`] `is_known_column` está exportable pero **nadie lo usa** (grep solo lo encuentra en su propia definición). Huérfano del refactor.

- [`evaluator.py:31`] `_read` retorna `float(val)` sin try/except — si una celda inesperadamente contiene un string (no debería en panels normalizados) lanza ValueError que sube hasta `_evaluate_close` y se loguea como `alerts.evaluate_close.error`. No es bug, pero el error message no apunta a la celda problemática.

- [`routes.py:165-186`] `list_events` no expone filtro por `kind` o `symbol`. Para F4, la UI seguro querrá filtrar "solo bias_promoted" — añadir query params es trivial.

- [`routes.py:141-161`] `delete_rule` retorna 204 incluso si la regla ya estaba `enabled=false` (idempotente, vale), pero el `rowcount` check no distingue "no existía" de "ya soft-deleted" — un re-delete sobre rule ya disabled retorna 404 ("not found") aunque exista. UX edge.

- [`evaluator.py:48`] `==` sobre floats es prácticamente inútil (`rsi_14 == 30.0` casi nunca cierto). No hay validación que advierta al usuario/agente. Considerar warning en `Condition.__post_init__` o tolerance.

- [`runtime.py:50-52`] `alerts_channel(user_id)` no sanea el user_id antes de meterlo en el nombre de canal. Si llegase un user_id con caracteres extraños (`:`, espacios), rompería el routing de WS. Hoy el user_id viene de BetterAuth (UUID), pero defensiva.

## ✅ Lo que está bien

- Separación pura/impura **excelente**: `evaluator.py` y `cooldown.evaluate_streak/evaluate_cooldown_verdict` son funciones puras → tests directos sin DB. Es exactamente el patrón que F4 necesita.

- `panel_service.compute_panel_for_specs` está bien factorizado: dedupe `_union_specs` + `_max_lookback` + delegación a `compute_panel`. Sin acoplamiento al runtime; ambos consumidores (AlertsRuntime, SetupRuntime) lo usan de forma simétrica (`setups/runtime.py:662`).

- `_SCOUT_TASKS` con strong-ref + done-callback discard — patrón correcto para `asyncio.create_task` fire-and-forget (evita GC race).

- `_compile_spec` cache invalidation por `(updated_at, RuleSpec)` — eficiente: re-valida solo cuando una PATCH bumpea `updated_at`.

- Cooldown logic (`should_pause_scout`) tiene tests pure-function cubriendo: streak breaks on win, breakeven positive, ventana límite inclusive, scope precedence (global gana a symbol). Buena cobertura para el módulo más bias-sensitive.

- Migración 003 con NOTIFY trigger es elegante — evita polling sobre `bias_events`. Solo falla en multi-replica (ver Critical #3).

- `RuleSpec` validación pydantic en write-time (con `model_validate` en `_compile_spec`) significa que reglas malformadas se filtran del panel union antes de eval — no crashea el loop.

- DSL deliberadamente **constreñido**, no free-text (operadores literales en `Operator: Literal`). Esto **elimina por construcción la categoría SQL-injection en DSL→SQL**: no hay parse de strings de usuario que termine en query.

- Validación de seguridad fundamental OK: `routes.py` usa siempre `WHERE user_id = :uid` con `require_user_id` dep; `delete_rule` y `patch_rule` filtran por user_id antes de UPDATE; `mark_event_seen` también.

## Notas adicionales

**Test gaps específicos para F4 24/7**:

1. **No hay tests de `_evaluate_close` end-to-end** con panel real. Solo se testea `evaluate_rule` (pure) y `_max_lookback`/`_union_specs` (pure). El path de runtime (fetch_active_rules → compute_panel → evaluate → record_hits → publish) no tiene test integration.

2. **No hay test del race "dos closes simultáneos del mismo TF"** (Important #1). Esto es exactamente el tipo de bug que F4 24/7 expone.

3. **No hay test de `_bias_listener_loop`** — ni de la suscripción ni del reconnect. Para F4, mockear `asyncpg.connect` con un fake que dispare `_on_notify` sería trivial.

4. **No hay test del cold-start**: arrancar `AlertsRuntime` con N reglas ya en DB → ¿se cargan? El test actual solo verifica funciones puras.

5. **No hay test que valide que `_evaluate_close` no leakea sesiones** si una excepción cae entre los dos `session_scope`. Importante para 24/7.

**Arquitectura sólida, deuda concentrada en concurrencia y multi-worker.** El módulo es claro y bien testeado en su core puro; los problemas reales aparecen en el runtime y en la asunción tácita de "un único worker". Antes de F4 24/7 (paper trading), los tres Critical merecen un PR dedicado:

1. Reorganizar `_evaluate_close` para liberar la sesión antes del compute_panel.
2. Defender `_record_bias_promoted` con validación de payload + user_id explícito (no default `'me'`).
3. Hacer el bias listener single-elected (lock por advisory en Postgres, o flag en config para correr solo en réplica 0).

Si el deployment objetivo de F4 es **un solo worker** (single-instance Railway), los puntos #3 críticos y #1 importante pierden urgencia, pero conviene documentarlo explícitamente en CLAUDE.md.
