# Audit: reviewer/

**Auditor**: general-purpose agent
**Fecha**: 2026-05-12
**Módulo**: apps/api/app/reviewer/

## 🔴 Critical

### C1. Test usa import path obsoleto `app.runtime.review_dispatcher` (post-PR9)
- `tests/runtime/test_review_observability.py:26` → `from app.runtime import review_dispatcher`. Ese módulo NO existe (la ruta canónica tras PR9 es `app.reviewer.dispatcher`). El test `test_review_event_names_documented` fallará con `ModuleNotFoundError`. Es exactamente el tipo de residuo de extract que esta auditoría busca: el resto de imports del módulo SÍ se migraron, este se olvidó.
- Fix: cambiar a `from app.reviewer import dispatcher as review_dispatcher`.

### C2. Bug de indentación en `_evaluate_price_review_triggers` (price_move branch)
- `setups/runtime.py:792-808` el bloque `if threshold_crossed and far_enough_from_last:` está sobre-indentado (8 extra spaces respecto al `if setup.entry_px:` parent). Funcionalmente Python lo acepta porque el bloque entero es coherente con esa indentación arbitraria, pero esto es frágil: cualquier merge/format pass que reflowee el `if` lo mueve fuera del scope y deja `_fire_review(price_move)` siempre activo o nunca activo. Ruff format probablemente lo dejará así porque es válido, pero un humano que añada una rama sibling rompe la lógica silenciosamente.
- Fix: re-indentar a 4 spaces consistentes con el `if setup.entry_px:` parent.

### C3. `claim_review_slot` UPDATE no incluye `FOR UPDATE` ni manejo explícito de race auto-commit
- `reviewer/repo.py:60-83`: el UPDATE es atómico a nivel de fila (Postgres garantiza row-level lock implícito en UPDATE), por lo que dos workers concurrentes NO pueden ambos pasar el WHERE — uno actualiza, el otro ve 0 rowcount. ESO está bien.
- **Pero** el `session_scope()` del caller (`dispatcher.py:119-125`) abre una transacción separada para el claim y la cierra ANTES de invocar al agente y persistir. Si la API call al agente tarda 30s y el proceso muere (OOM, deploy, cancel) entre el claim y el `insert_review`, `last_review_attempt_at` queda actualizado pero NUNCA se persistirá un review → el setup queda "claimed" durante `cooldown_minutes` (default 30) sin que ningún review llegue al journal. La doc del claim (`repo.py:55-58`) reconoce esto: "Si el agente falla, `last_review_attempt_at` ya quedó actualizado y previene re-intentos inmediatos" — es deliberado pero es el comportamiento más doloroso (entry_hit que nunca produce review post-mortem visible). Considerar reducir `cooldown_minutes` a 2-5 min cuando el motivo fue un crash, o un "rollback del claim" en `except Exception`.
- **Severity bump**: la doc dice "atomic cooldown claim. UPDATE condicional" pero NO menciona que en caso de fallo del agente, el setup queda bloqueado 30 min. Eso es contra-intuitivo respecto al wording "cooldown" (que sugiere "ya hicimos review, espera"), cuando aquí significaría "intentamos y falló, espera".

## 🟡 Important

### I1. Semaphore se crea con `Settings` ya bound, no actualizable en runtime
- `dispatcher.py:46-50`: `_get_semaphore()` lee `review_concurrency` la primera vez que se invoca y queda bound. Si en tests cambias `REVIEW_CONCURRENCY` vía env override después del primer `_fire_review`, no se respeta. No es un bug en prod (la env es fija), pero los tests no pueden modificar concurrency in-process. La variable `_concurrency_lock` (`dispatcher.py:42`) está declarada pero NUNCA usada — código muerto.
- Fix: borrar `_concurrency_lock`. Considerar `reset_semaphore()` helper para tests.

### I2. Re-check `_setup_is_open_for_review` corre DESPUÉS de adquirir el semáforo
- `dispatcher.py:138-146`: el re-check status corre dentro del `async with sem`, lo cual ocupa un slot del semáforo (default=2) durante una query trivial sólo para descubrir que el setup cerró. Si 5 setups en cooldown-claim-success cierran en cadena entre la dispatch y la lectura, los 2 slots del semáforo bloquean a otros reviews legítimos por esa milisegunda. No es crítico pero el orden ideal sería: claim → re-check status → adquirir sem → invoke. La verificación es barata.

### I3. `_extract_usage_and_cost` y `_setup_is_open_for_review` están en `dispatcher.py`, no testeados aisladamente
- El test `test_review_cost.py` cubre `_extract_usage_and_cost` con stubs (correcto). Pero `_setup_is_open_for_review` no tiene cobertura — y es el guard que evita reviews sobre setups closed. Tampoco hay test del `claim_review_slot` (atomicidad / cooldown). Para concurrencia (el bloque más complejo del módulo), la cobertura es 0% real.
- **Gap explícito**: `tests/runtime/test_review_cooldown.py` y `tests/runtime/test_review_concurrency_semaphore.py` no existen. La doc del dispatcher (línea 1) anuncia "atomic cooldown claim" pero ningún test lo prueba.

### I4. `get_last_reviews` no scope-a por user_id
- `repo.py:274-297`: query SELECT `trade_id = :tid` sin `AND user_id = :uid`. El caller en `dispatcher.py:151` es trusted (proviene del SetupRuntime que sí filtra por journal_trades.user_id en list_open_setups), pero la "defensa en profundidad" presente en `list_reviews_for_setup` y `get_review` no se aplica aquí. Si un día se reusa esta función desde un endpoint REST sin envoltura, leak cross-user.

### I5. `_normalize_review_row` tiene una guard redundante / mal escrita
- `repo.py:373`: `if (isinstance(out.get("price_at_review"), (int, float)) is False and out.get("price_at_review") is not None) or out.get("price_at_review") is not None`. Lógicamente esto es **siempre verdadero cuando price_at_review is not None** — la OR colapsa en la segunda condición. No es un bug (el float() casting es idempotente sobre int/float/Decimal) pero el código está roto semánticamente; el intent era "casteá si es Decimal o str". Simplificar a `if out.get("price_at_review") is not None: out["price_at_review"] = float(out["price_at_review"])`.

### I6. Tests del scheduler asumen `compute_next_review_at` con `entry_hit_at` tz-aware
- `tests/runtime/test_review_scheduler.py`: todos los entries son `datetime(..., tzinfo=UTC)`. Pero `OpenSetupRow.entry_hit_at` viene de Postgres y puede ser naive si la columna no se declaró `timestamptz`. Migración 009 (`009_trade_reviews.py:51`) usa `timestamptz` correctamente → en prod siempre tz-aware. El test es coherente con prod, pero no hay cobertura del caso "naive datetime crash" — si alguien añade un campo `timestamp` no-tz por accidente, lo verás solo en run-time.

## 🟢 Minor / cleanup

### m1. Dead variable `_concurrency_lock` (dispatcher.py:42)
- Declarada pero nunca usada. Borrar.

### m2. Cost cálculo asume `chargeable_input = input_t - cache_read`
- `dispatcher.py:449`: la fórmula correcta de Anthropic separa cache_create (tokens escritos al cache) del input regular. Aquí `cache_write` se loguea en usage_tokens pero NO se charge — Anthropic cobra cache_creation a 1.25× del input rate. Sub-billing pequeño (cache_create suele ser ≪ input en steady state) pero presente. Doc-string admite "best-effort" así que low priority.

### m3. `setups/runtime.py:88-107` y `:112-134` son helpers duplicados (_fire_review vs _fire_post_mortem)
- Patrón calcado. No es del módulo `reviewer/` per se, pero indica que un `_fire_background_task(name, coro)` helper unificaría ambos sin perder lifecycle behavior. Out-of-scope para esta auditoría.

### m4. Magic number `_REVIEW_SCHEDULE_INTERVAL_S = 300.0` en runtime.py
- Hardcoded, no expuesto en Settings. El comentario lo justifica ("5 min") pero un override sería trivial. Low priority.

### m5. `dispatcher.py:179` `result = await get_review_agent().run(prompt, deps=deps)` no respeta timeout
- Si OpenRouter está colgado, la tarea queda bloqueada indefinidamente ocupando un slot del semáforo. `asyncio.wait_for(..., timeout=settings.review_timeout_s)` daría hard-bound. Settings no expone `review_timeout_s`.

### m6. `_normalize_setup_row` (repo.py:387-401) duplicado parcial con `OpenSetupRow` factory en `list_open_setups`
- El normalizador del scheduler convierte Decimal→float y string→json en `targets/confluences/scenarios`, lógica que en `setups/repo.py::list_open_setups` ya se hace con Pydantic validation. Refactor opcional: usar `OpenSetupRow.model_validate` para uniformar.

### m7. `dispatcher.py:178` `result = await get_review_agent().run(...)` dentro del `async with sem:` significa que la persistencia (`insert_review`) sale del semáforo **después** del agent.run pero antes de soltar el `with` block — el código está OK pero el comentario "8) Persist" sugiere que está fuera del semáforo, cuando líneas 195-210 ya están post-sem (de hecho `async with sem:` cierra en :181 cuando `review` se asigna). Verificar manualmente el indent: efectivamente la persistencia ocurre tras soltar el semáforo. OK, solo confuso visualmente.

## ✅ Lo que está bien

- **Imports residuales tras PR9 (en código de producción)**: limpios. Todos los `from app.reviewer.X` correctos en `dispatcher`, `agent`, `validators`, `system_prompt`, `repo`. Los `from app.agent.X` que quedan son legítimos (compartir `AgentDeps`, `TradeReview` model, tool registrars).
- **Wiring**: `SetupRuntime._fire_review` (runtime.py:86-107) → `maybe_run_review` → confirmed. Triggers cubiertos: `entry_hit` (runtime.py:481), `tp_partial` (608), `price_move` (797), `approaching_sl` (822), `time_elapsed` (928). `regime_change` está declarado en TriggerKind y system_prompt pero NO dispatched desde runtime — gap pero documentado en blueprint (presumiblemente F5+).
- **Pub/sub channel**: `reviews:user:{user_id}` exacto (`pubsub.py:49`), reusado por post_mortem con `type='post_mortem'` (correcto — el frontend escucha un único canal).
- **Output independence**: `review_agent` no extiende el `output_type` del main agent. Verificado en `reviewer/agent.py:55` (`output_type=TradeReview` puro) vs `agent/agent.py:73` (`BriefAnalysis | TradeIdea | str`).
- **Tools subset**: confirmado 7 tools (`reviewer/agent.py:68-74`): confluence, correlation, indicators, ohlcv, perps_data, structure, volume_profile. NO incluye log_trade/journal_query/biases/run_backtest/walk_forward/cpcv/factor_stats/strategy_metrics/create_alert/list_alerts/delete_alert/basis/dominance/perps_dynamics/similar_setups — matches la pauta del CLAUDE.md.
- **System prompt size**: `reviewer/system_prompt.py` ~195 líneas vs `agent/system_prompt.py` significativamente más grande — cache-friendly. Version tag `rv2` presente.
- **Validators reviewer-específicos**: `validators.py:71-89` cubren las dos incoherencias state↔recommendation que el blueprint pide (reversing+hold, on_track+exit_now). Tests `test_review_validators.py` cubren ambos cases + tool_name discriminator.
- **REVIEW_TIME_OFFSETS_H parsing**: `config.py:189-199` property `review_time_offsets_list` parsea CSV → tuple[int,...], sortea + dedupea. Tests `test_review_scheduler.py:51-58` cubren orden desordenado.
- **Cost telemetry**: `_extract_usage_and_cost` (dispatcher.py:414-455) lee usage(), aplica pricing per-million desde Settings, soporta cache_read discount. Tests `test_review_cost.py` cubren 3 cases.
- **user_id scoping en queries críticas**: `list_reviews_for_setup` (repo.py:228-234) y `get_review` (repo.py:255-263) ambos filtran `user_id = :uid`. INSERT también persiste user_id.
- **Pub/sub channel incluye user_id**: `reviews_channel(setup.user_id)` (dispatcher.py:214) — no cross-user leak.
- **Cap pre-check + atomic claim**: dispatcher.py:95-132 separa cap_reached (read-only, mejor log) del claim atómico. Pattern defensible.
- **Fire-and-forget task lifecycle**: `_REVIEW_TASKS` set + `add_done_callback(discard)` previene GC prematuro y leaks (runtime.py:83-107).
- **OPENROUTER_API_KEY**: leído vía `get_settings().openrouter_api_key` (agent.py:39), RuntimeError si missing — correcto.

## Notas adicionales — atomicidad de `claim_review_slot` verificada

El UPDATE condicional (`repo.py:60-83`) es **realmente atómico** bajo Postgres: cada fila adquiere ROW EXCLUSIVE lock implícitamente cuando el WHERE evalúa, y dos workers concurrentes con el mismo trade_id se serializan a nivel de planner. El segundo verá los valores post-UPDATE del primero (cooldown bumped) y rebotará. Probado por construcción Postgres, no por test — recomiendo añadir un integration test `test_claim_review_slot_concurrent.py` que abra 2 sesiones y reclame paralelo.

**Race window real**: entre `claim_review_slot` (transacción 1, dispatcher.py:119-125) e `insert_review` (transacción 2, dispatcher.py:195-209) hay 5-30 segundos de agent.run(). Durante esa ventana, otro worker que llegue verá `last_review_attempt_at` reciente (claim ya bumped) y rebotará — correcto, no doble-dispatch. Si el primer worker crashea durante agent.run(), el setup queda "claimed sin review" hasta cooldown_minutes (C3). Esto es trade-off documentado, no bug — pero merecería un test del flow + posiblemente un script de mantenimiento `unstuck_claimed_setups.py`.

**Pre-F4 readiness**: con C1 + C2 arreglados y un test de cooldown concurrency añadido (I3), el módulo está listo para F4. La deuda de timeout (m5) y rollback-on-failure (C3) puede atacarse incrementalmente en producción una vez se observe el comportamiento bajo carga real.
