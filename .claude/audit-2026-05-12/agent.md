# Audit: agent/

**Auditor**: general-purpose agent
**Fecha**: 2026-05-12
**Módulo**: apps/api/app/agent/

## 🔴 Critical (bloqueante para F4)

- [apps/api/app/agent/routes.py:347] `datetime.utcnow().isoformat() + "Z"` se interpola dentro de cada preamble. Esto se inyecta en el **user message** (no en system_prompt) así que NO rompe el cache de Anthropic, pero produce un timestamp ISO sin tz-info que viola la invariante del módulo ("as_of timestamps: TZ-aware"). Más importante: `utcnow()` está deprecated en Py3.12+ y emite DeprecationWarning. **Por qué bloquea**: F4 corre 24/7 con scout dispatcher reutilizando los mismos paths; un DeprecationWarning sobre código que se ejecuta en cada turno contamina logs y eventualmente se romperá. Usa `datetime.now(tz=UTC).isoformat()`.

- [apps/api/app/agent/agent.py:108-115] El singleton `_agent_instance` se construye lazy sin lock. `get_agent()` se llama desde 4 sitios concurrentes en F4: `agent/routes.py::chat` (FastAPI worker), `setups/scout_dispatcher.py::run_for_user`, `reviewer/dispatcher.py`, `post_mortem/dispatcher.py`. **Por qué bloquea**: si el primer warm-up ocurre con N requests llegando simultáneamente al boot (escenario real en Railway tras un redeploy), `build_agent()` se ejecuta N veces y se asignan N instancias antes de que la última gane. Cada `build_agent()` registra ~25 tools sobre un Agent fresco → race benigno en outcome pero consume tiempo de boot y, peor, perdiendo el contrato "byte-stable system prompt" si la API key cambia entre llamadas. Solución: `asyncio.Lock` o construirlo eager en `lifespan` startup (preferible — alinea con `LiveIngestion`/`AlertsRuntime`).

- [apps/api/app/agent/agent.py:81-102] Las llamadas a `register_*` NO están en orden alfabético (orden: ohlcv, indicator, structure, basis, confluence, …). CLAUDE.md declara: *"Tools are registered in alphabetical order (cache-prefix stability) from app/agent/tools/\*.py"*. **Por qué bloquea**: Pydantic-AI ordena las tools en el system schema por el orden de registro. Cambiar el orden invalida el cache prefix de Anthropic en cada deploy donde alguien re-toque agent.py. Esto ya está mal pero "funciona" hoy porque el orden es estable; el riesgo F4 es que un refactor inocente futuro re-ordene y resetee el cache silenciosamente, multiplicando costes ×3-5. Fija invariante alfabética en código (ordena las líneas) y considera test de regresión sobre el catalog del agent.

- [apps/api/app/agent/tools/{cpcv,run_backtest,strategy_metrics,walk_forward,list_alerts,delete_alert}.py] 8 ocurrencias de `datetime.now()` SIN `tz=UTC` (cpcv.py:98, run_backtest.py:97, strategy_metrics.py:48,74,128, walk_forward.py:100, list_alerts.py:49, delete_alert.py:44). Generan `Provenance.as_of` naive (TZ-unaware). **Por qué bloquea F4**: `Provenance.as_of` es el ancla auditada de cada citation y se persiste/serializa cross-process (validator → setup_runtime → frontend WS). Mezclar TZ-aware (mayoría) con TZ-naive (estos 8) provoca `TypeError: can't compare offset-naive and offset-aware datetimes` en cualquier comparación. Es el bug clásico que aparece exactamente cuando el scout 24/7 empieza a comparar `as_of` con cutoffs.

- [apps/api/app/agent/tools/strategy_metrics.py:53-115] `get_strategy_metrics` lee `backtest_runs` sin filtrar por `user_id`. Schema confirmado: `backtest_runs` (migración 002) NO tiene columna `user_id` (`ls alembic/versions/002_journal_and_backtests.py:93-110`). El mismo issue afecta `run_backtest`, `run_walk_forward`, `run_cpcv`. **Por qué bloquea F4**: multi-tenant data leak. Cuando el primer co-piloto B opere en paralelo, podrá citar run_ids del usuario A (y, peor, el factor_gate del validator A usaría stats globales). El validator confía en el `tool_name+run_id` como receipt — sin user_id scoping, A cita un run_id que existe pero pertenece a B; la validación pasa pero el "edge" no es del usuario. F4 paper trading depende de stats fiables del usuario.

## 🟡 Important (atender pronto, no bloqueante)

- [apps/api/app/agent/routes.py:113-251] La función `_inject_historic_stats_preamble` muta `request._body` (atributo privado de Starlette). Funciona hoy pero queda al margen del contrato público. Si Starlette cambia el lazy-body mechanism, la inyección pasa silenciosa (el `except Exception` lo traga). Considera un middleware/dependency que parsee el body antes de pasarlo al adapter.

- [apps/api/app/agent/routes.py:60-67] `getattr(settings, "exchange", "binance_usdm")` siempre cae al default — `Settings` no tiene atributo `exchange` (verificado en `app/core/config.py`). El `exchange` que se pasa a `_inject_historic_stats_preamble` luego NO se propaga a la `AgentDeps` que construye en l. 52 (que usa el default `"binance_usdm"` de `deps.py:25`). Resultado: dos sources of truth para el exchange. Si en F4 quieres soportar otro venue, hay que tocar dos sitios. Centraliza en `Settings.exchange` o solo en `AgentDeps`.

- [apps/api/app/agent/validators.py:1228] `from app.core.config import get_settings` dentro de un branch del validador. Import local presumiblemente para evitar import cycle con `app.setups.repo` (línea 39). Verificado: no hay cycle real, es defensive. Mueve al top-level (es una llamada por validation, no hot path).

- [apps/api/app/agent/validators.py:1313-1342] `insert_setup_from_idea` se ejecuta DENTRO del output_validator. El validator es la última línea de defensa antes de devolver el output al user; mover persistencia ahí mezcla concerns y deja el side-effect a merced de futuros refactors del validator chain. Side note: ya hay try/except logueante, pero un fallo silencioso impide que F4 reciba el setup en SetupRuntime — el chat responde pero el setup nunca se materializa. Considera mover a un hook post-validator o a un `app.agent.events` separado.

- [apps/api/app/agent/validators.py:496-524, 527-588, 591-628] Tres funciones helper recorren `ctx.messages` SECUENCIALMENTE (4 pasadas: handles, outputs, stale warnings, confluence data, lvn zones, high biases). Cada una itera todos los messages del turn. No es un problema en single-turn, pero el validator se ejecuta tras CADA retry (hasta 2 retries × varios validator paths) — O(N × messages × parts). Para chats con muchas tool calls (max 8 × paginado), considera UN solo `_collect_turn_context(messages) -> dataclass` que recoja todo en una pasada.

- [apps/api/app/agent/validators.py:178] `_find_numeric_values` no distingue entre `bool` y `int` aunque la guard `isinstance(v, bool): continue` está. PERO en `_walk_handles` (línea 91) no hay esta guard: si un payload contiene `{"id": True}` se trataría como handle string falsy y se filtra por `isinstance(v, str) and v` → ok. Verifica que ninguna tool emite `id` o `run_id` como bool/int.

- [apps/api/app/agent/agent.py:43,66,69,79] `DEFAULT_MODEL_ID`, `max_tokens=24000`, `thinking="medium"`, `retries=2` hardcoded en código. CLAUDE.md y `REVIEW_*` envvars sugieren que Reviewer/Post-mortem leen su config de Settings — el main agent NO. Para F4 (paper trading 24/7 con scout autónomo) querrás poder tunear `max_tokens` y `retries` sin re-deploy. Promueve a Settings (`AGENT_MAX_TOKENS`, `AGENT_THINKING`, `AGENT_RETRIES`).

- [apps/api/app/agent/models.py:39] `snapshot: dict[str, Any]` — el `Any` es necesario para la flexibilidad de los snapshots heterogéneos (números, strings, listas) que el LLM cita. Está justificado por la verificación posterior del validator. OK.

- [apps/api/app/agent/tools/_envelope.py:21-22] `Provenance.as_of: datetime` no enforce tz-awareness (no usa `AwareDatetime`). Junto con los `datetime.now()` naive del Critical anterior, te quedas sin defensa "schema-level". Usa `pydantic.AwareDatetime`.

- [apps/api/app/agent/tools/biases.py:85, factor_stats.py:257] Fallback a `datetime.fromtimestamp(0)` (epoch) sin `tz=UTC` cuando no hay data → datetime naive. Esto rompe luego cualquier comparación con `now(tz=UTC)`. Cambia a `datetime.fromtimestamp(0, tz=UTC)` o `datetime.min.replace(tzinfo=UTC)`.

- [apps/api/app/agent/tools/log_trade.py:41-98] `log_trade` recibe `entry_px`, `size`, `exit_px` como `float`. F4 paper trading va a comparar estos contra fills reales en USD-M perps (precios típicos con 5-7 dígitos significativos: 67234.123). `float` IEEE-754 da 15-17 sig digits, acceptable, pero `r_multiple` calculations heredan errors. Considera `Decimal` para campos monetarios persistidos; el agente puede emitir float y el repo convertir.

- [apps/api/app/agent/system_prompt.py:476] El system prompt dice `"NEVER call >6 tools in a single analysis (signal/noise threshold)."` pero la sección anterior (l. 217) dice `"Máximo 8 tool calls por análisis"`. Inconsistencia interna en el system prompt — el LLM ve dos números contradictorios y elegirá uno al azar. Unifica a 8.

- [apps/api/app/agent/tools/structure.py:72] Comentario contiene "TODOS" en mayúsculas que ruff podría detectar como TODO marker (lo encontré con `grep "TODO"`). No es un TODO real pero confunde grep-based tooling. Renombra a "todos los demás" lower-case o usa comilla.

## 🟢 Minor / cleanup

- [apps/api/app/agent/agent.py:43] Comentario "Opus 4.7 (Deep-dive) is plumbed in but not toggled" — verifica si efectivamente está plumbed o es dead doc.

- [apps/api/app/agent/routes.py:80-90] Headers (`Cache-Control`, `X-Accel-Buffering`, `Connection`) se aplican post-response. Si `VercelAIAdapter.dispatch_request` ya los setea (verificar versión de pydantic-ai), tienes overrides redundantes. No es bug, solo posible deuda.

- [apps/api/app/agent/tools/_envelope.py:25-28] `warnings: list[str]` sin enforce shape. Los validators harvested `provenance.warnings` con prefijo `"stale:"` — formaliza con un Literal/enum o helper para evitar typos silenciosos.

- [apps/api/app/agent/validators.py:407-427] `_ALLOWED_SEMANTIC_TAGS` y `_SEMANTIC_TAG_REQUIREMENTS` (línea 247) están en dos sitios — la addition de un tag implica tocar ambos sets y es fácil olvidar uno. Considera una clase `SemanticTag(name, requirement?, ...)`.

- [apps/api/app/agent/validators.py:737-744] El soft-degrade de `confidence` muta `output.confidence` in-place. Pydantic models are mutable por defecto, pero un test futuro que asuma immutability se sorprenderá. Documenta o usa `model_copy(update={...})`.

- [apps/api/app/agent/system_prompt.py:8-12] Docstring dice "The LAST block carries the cache_control marker" pero `build_system_blocks` (l. 834) solo concatena strings sin pasar markers segmentados. El comentario sobra hasta que pydantic-ai exponga cache_control explícito (ver línea 838-843 que ya nota esto).

- [apps/api/app/agent/tools/log_trade.py] No hay tool `update_trade` ni mecanismo para corregir un trade mal-logged. F4 va a generar fills que pueden cambiar (slippage, partial fills); pensar la API ahora.

- [apps/api/app/agent/models.py:18] `Timeframe = Literal["15m", "1h", "4h", "1d"]` — no incluye `30m`, `2h`, ni minutiae sub-15m. F4 paper trading puede necesitar `5m`/`1m` para entry tactics. Decide si scope-creep es ok o si codificas restrictivamente.

- [apps/api/app/agent/deps.py:25] `exchange: str = "binance_usdm"` con default value pero ni `Settings` ni `routes.py` lo override en la práctica. Tres sources of truth para "binance_usdm" (deps.py:25, routes.py:65, envelope.py:19 docstring). Centraliza.

## ✅ Lo que está bien

- Separación de **reviewer/** y **post_mortem/** terminó limpia: ningún archivo en `agent/` importa `agent.reviewer.*` o `agent.post_mortem.*`. Las inversas (reviewer/post_mortem importan de agent/) están bien tipadas y siguen el patrón hub-and-spoke esperado (el agent es la fuente de modelos y tools compartidas).

- `chat_router` correctamente montado en `app/main.py:80`. Sin ciclos de import detectados (`agent → core/auth, core/db, core/config, alerts/dsl, backtest/factor_stats_repo, post_mortem/repo, setups/repo` — todos forward refs, sin retorno).

- Citation validator es robusto: 3 layers (tool_name discriminator, handle existence en `_walk_handles`, snapshot numeric tolerance 0.1% en `_verify_snapshot_numerics`). Soft-degrade vs ModelRetry calibrado según severity. Tests `test_validators_citation_rigor.py` cubren los happy/sad paths principales.

- `OpenRouterProvider` se construye con `api_key=get_settings().openrouter_api_key` explícito (línea 47-57) — no `os.environ`. Falla loud si la key falta (no silently). 

- `async with deps.session_factory()` es el patrón consistente en todos los tools que tocan DB (`ohlcv.py:45`, `structure.py:175`, `confluence.py:217`, etc.). No detecté ningún tool que mantenga session viva entre operaciones.

- `user_id` propagado correctamente en las queries con writes: `create_alert.py:55` (INSERT con `:uid`), `delete_alert.py:32` (WHERE user_id), `list_alerts.py:40` (WHERE user_id), `log_trade.py:57` (JournalTradeIn carries user_id), `journal_query.py` y `similar_setups.py` filtran por `ctx.deps.user_id`.

- System prompt NO interpola timestamps ni per-request data (verified: `build_system_blocks()` solo concatena TOOLS_CATALOG + COPILOT_RULES + profile JSON, todas constants). Las per-request inyecciones viven en `routes.py::_inject_historic_stats_preamble` que muta el USER message, no el system block — invariante respetada.

- Tests no importan paths viejos del refactor. `test_review_*` apuntan correctamente a `app.reviewer.*`, `test_post_mortem_validators` apunta a `app.post_mortem.validators`. Los tests de validators principales (test_validators_citation_rigor, test_factor_gate) capturan el validator function vía mock `_CaptureAgent` — desacoplados del Agent real.

- `staleness_warning` en `_time.py:37-47` propaga el `"stale:"` warning prefix consistentemente a través de `provenance.warnings`, y `validators._collect_stale_tool_warnings` lo cosecha — feedback loop cerrado.

- `agent.py:73-80` enforce `Agent[AgentDeps, BriefAnalysis | TradeIdea | str]` con tipado explícito; `output_type` matches en `routes.py::get_agent()` y en `validators.py::register_validators` signature.

## Notas adicionales

- **No hay huérfanos en `agent/`**: todos los archivos top-level (agent, deps, models, routes, system_prompt, validators, trader_profile) tienen referencias entrantes desde main.py o desde reviewer/post_mortem. Todas las `tools/*.py` se importan desde `agent.py`.

- **Tests-gaps identificados**:
  - No hay test del invariante "tools registered alphabetical → cache prefix stable". Dado el Critical #3, este test ayudaría a evitar futuras regresiones silenciosas. Sugerencia: snapshot test sobre `agent.toolset` (el orden visible al modelo).
  - No hay test para `routes.py::_inject_historic_stats_preamble` — la mutación de `request._body` es lo más frágil del módulo y no está cubierto.
  - No hay test cross-tool sobre TZ-awareness de `Provenance.as_of` — un test parametrizado que registre todas las tools, llame con session stub y verifique `as_of.tzinfo is not None` cazaría los 8 Critical de `datetime.now()`.
  - No hay test multi-user para validar que `user_id` scoping funciona end-to-end (sobre todo en backtest_runs donde NO existe la columna).

- **Riesgo cripto-tipográfico**: la mención en CLAUDE.md de que el modelo es `anthropic/claude-sonnet-4.6` (DOT, no DASH) está respetada en `agent.py:43`. Ojo si en F4 introduces un selector dinámico de modelo — la convención dot vs dash de OpenRouter es trampa frecuente.

- **Confluence helper público** (`compute_score_components`, l. 404-449) está bien tipado y usado correctamente por routes.py:158 y por el validator factor_snapshot pipeline. Es el patrón "tool wrappers re-usable sin envelope" que F4 va a querer extender — mantenlo.

- **Para F4**: el principal vector de riesgo del módulo es la combinación de (a) singleton sin lock + (b) `_agent_instance` regenerado por test fixtures + (c) registro de tools no-determinista en orden. Cuando el scout 24/7 ataque el agent desde múltiples coroutines simultáneas justo tras un cold start, podrías ver el bug "tools no registradas en este Agent instance" si el race entre instances ocurre. Recomiendo eager construction en `lifespan` startup.
