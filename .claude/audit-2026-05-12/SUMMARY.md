# SUMMARY — Auditoría profunda backend post-refactor modular

**Fecha**: 2026-05-12
**Alcance**: 12 módulos en `apps/api/app/` + tests + migraciones + wiring frontend
**Auditores**: 12 agentes paralelos `general-purpose`
**Pre**: F4 (paper trading 24/7) y bot autónomo

---

## TL;DR — Verdicto

🛑 **NO ir a F4 sin antes resolver los bloqueantes**. El refactor PR8–PR11 (extract reviewer/post_mortem, consolidar paper_trading, modularizar web/lib) **estructuralmente está limpio** (imports OK, routers cableados, sin ciclos), pero la auditoría detecta **dos categorías de problema serias**:

1. **paper_trading/ no es un motor de paper trading** — es solo slippage simulation. F4 requiere 5–10× más código + tablas (`paper_orders`, `paper_positions`, `paper_balance`, `paper_equity_curve`). Esto **reordena el alcance de F4**: antes de "24/7 autónomo" hay que **diseñar y construir** el engine.
2. **Cross-cutting bugs** que comprometerían multi-tenant + 24/7 stability: `user_id` scoping ausente en 7+ sitios, race conditions de concurrencia, `datetime` TZ-naive, tests rotos silenciosamente.

**Recomendación**: parar y atacar los 4 patrones cross-cutting + el diseño del paper engine antes de continuar. Estimación: 1–2 semanas de trabajo focused.

---

## Tabla resumen por módulo

| Módulo | 🔴 Critical | 🟡 Important | 🟢 Minor | Estado |
|---|---|---|---|---|
| `agent/` | 5 | 11 | 7 | 🟠 deuda concentrada en concurrencia + TZ |
| `alerts/` | 3 | 10 | 12 | 🟠 multi-worker + cooldown race |
| `backtest/` | 3 | 5 | 7 | 🟠 DSR formula divergent + user_id ausente |
| `core/` | 3 | 6 | 8 | 🔴 **0 tests para auth/db/config/pubsub** |
| `journal/` | 4 | 8 | 11 | 🔴 cross-user leak en `get_by_id` |
| `market/` | 4 | 10 | 8 | 🔴 **tests rotos silenciosos** + `/ws/market` sin auth |
| `notifications/` | 3 | 6 | 7 | 🟠 UNIQUE missing en chat_id |
| `paper_trading/` | 5 (todos bloqueantes F4) | 7 | 8 | 🛑 **el módulo NO es un engine completo** |
| `platform_routes/` | 0 | 4 | 7 | ✅ limpio (módulo nuevo post-PR10) |
| `post_mortem/` | 0 | 5 | 9 | ✅ bien cableado, falta tests dispatcher |
| `reviewer/` | 3 | 6 | 7 | 🟠 test residual del extract + claim sin rollback |
| `setups/` | 5 | 9 | 8 | 🔴 rate-limit bypass scout + indentación frágil |
| **TOTAL** | **38** | **87** | **99** | — |

---

## Patrones cross-cutting (lo más serio)

### Patrón A — `user_id` scoping ausente (8 módulos)

Riesgo: **multi-tenant leak / hijacking**. Cuando F4 abra a >1 usuario, datos cruzan fronteras silenciosamente.

| Lugar | Severidad |
|---|---|
| `journal/repo.py::get_by_id` y `list_all_for_embed_check` | 🔴 Critical (cross-user read) |
| `journal/repo.py::_HYBRID_SQL` JOIN setup_post_mortems sin filtro pm.user_id | 🔴 Critical (defense in depth) |
| `backtest_runs` y `strategy_metrics` no tienen columna `user_id` (migración 002) | 🔴 Critical (DSR cross-tenant infla `n_runs`) |
| `agent/tools/{strategy_metrics,run_backtest,walk_forward,cpcv}.py` no filtran user_id | 🔴 Critical (validator pasa con run_id ajeno) |
| `paper_trading/repo.py::aggregate_observed_slippage_p75` query global | 🟡 Important (calibración cross-user) |
| `notifications/repo.py` sin UNIQUE en `telegram_chat_id` | 🔴 Critical (chat hijacking → approve setup ajeno) |
| `reviewer/repo.py::get_last_reviews` sin AND user_id | 🟡 Important (defense-in-depth) |
| `journal/bias_events` sin FK CASCADE a journal_trades | 🟡 Important (huérfanos por user) |

**Fix global**: PR dedicado "multi-tenant scoping" que (a) añada `user_id` a las 3 tablas que faltan + migración, (b) refactorice tools/repos para tomar `user_id` requerido, (c) tests parametrizados con 2 users que verifiquen aislamiento end-to-end.

### Patrón B — Concurrencia / race conditions (7 módulos)

Riesgo: **24/7 lo va a romper**. Hoy "funciona" porque la carga es baja y hay 1 worker; F4 expone los races.

| Lugar | Tipo |
|---|---|
| `alerts/runtime.py::_evaluate_close` mantiene session abierta durante compute_panel | 🔴 pool exhaustion |
| `alerts/runtime.py::_bias_listener_loop` duplica eventos con N workers | 🔴 single-elected listener missing |
| `alerts/runtime.py` cooldown sin claim atómico (mismo pattern que reviewer ya resolvió) | 🟡 doble dispatch |
| `reviewer/dispatcher.py` claim sin rollback → setup bloqueado 30min si agent crashea | 🔴 stuck claims |
| `setups/runtime.py` race cross-task invalidation ↔ entry_hit cross-symbol/tf | 🔴 estado inconsistente |
| `agent/agent.py` singleton sin lock (4 callers concurrentes en F4) | 🔴 race init |
| `backtest/runner.py:290-307` SELECT/UPSERT de n_runs sin FOR UPDATE | 🔴 off-by-one DSR |
| `core/db.py::init_engine` no thread-safe (race en cold-start) | 🟡 pool leak |
| `core/pubsub.py` sin `subscribe_resilient`, cada caller reimplementa retry | 🟡 desconexiones perdidas |
| `journal/bias_detector.py::run_for_user` DELETE+INSERT sin atomic | 🟡 duplicados |
| `market/ohlcv/ingestion_live.py` gap_fill sin lock por (symbol, tf) | 🟡 doble fetch REST |

### Patrón C — `datetime` TZ-naive (3 módulos, 11+ ocurrencias)

Riesgo: **`TypeError` al comparar TZ-aware vs naive**. Bug clásico que aparece exactamente cuando el scout 24/7 cita `Provenance.as_of` cross-process.

| Lugar | Tipo |
|---|---|
| `agent/tools/cpcv.py:98`, `run_backtest.py:97`, `strategy_metrics.py:48,74,128`, `walk_forward.py:100`, `list_alerts.py:49`, `delete_alert.py:44` (**8 ocurrencias**) | `datetime.now()` sin `tz=UTC` |
| `agent/routes.py:347` preamble | `datetime.utcnow()` deprecated Py3.12+ |
| `setups/runtime.py:979` fallback | `datetime.utcnow()` |
| `agent/tools/biases.py:85`, `factor_stats.py:257` | `datetime.fromtimestamp(0)` sin tz |
| `agent/tools/_envelope.py::Provenance.as_of` no usa `pydantic.AwareDatetime` | sin enforce schema-level |

**Fix**: PR pequeño, replace mecánico + adopt `pydantic.AwareDatetime`. Bajo riesgo, alto valor.

### Patrón D — Tests rotos o ausentes en código crítico

Riesgo: **regresiones silenciosas**. Las áreas más sensibles tienen menos cobertura.

| Lugar | Estado |
|---|---|
| `tests/indicators/test_panel.py` | 🔴 **3 failed silently** — `app.indicators.panel` → `app.market.indicators.panel` (post-refactor) |
| `tests/runtime/test_review_observability.py` | 🔴 import `app.runtime.review_dispatcher` (obsoleto post-PR9) |
| `tests/core/` | 🔴 **0 tests** para auth/, db.py, config.py, pubsub.py |
| `tests/journal/embeddings|repo|routes` | 🔴 **0 tests** |
| `tests/post_mortem/` | 🟡 0 tests del dispatcher (más complejo del módulo) |
| `tests/reviewer/test_claim_review_slot` | 🟡 falta — atomicidad por construcción, no asserted |
| `tests/market/test_ingestion_live` | 🔴 0 tests live ingestion (gap_fill, reconnect, WS) |
| `tests/market/test_ws_routes` | 🔴 0 tests WS handler |
| `tests/paper/` | 🔴 cubre <10% de lo necesario para F4 (sin PnL absoluto, partial fills, concurrency, multi-user) |
| `tests/observability/test_health.py` | 🟡 0 tests HTTP del endpoint |

---

## Top 10 Críticos (priorizado)

| # | Módulo | Hallazgo | Razón | File |
|---|---|---|---|---|
| 1 | paper_trading | **El módulo no es un motor**. Falta engine completo (positions, orders, balance, equity) | F4 ES esto | `paper_trading/engine.py` + ausencia de tablas |
| 2 | journal | `get_by_id` y `list_all_for_embed_check` sin `user_id` → cross-user leak | F4 multi-tenant | `journal/repo.py:181-195, 225-248` |
| 3 | backtest | `backtest_runs`/`strategy_metrics` sin columna `user_id` | DSR cross-tenant + privacy | migración 002 + `runner.py:291-298` |
| 4 | agent | 8× `datetime.now()` sin tz=UTC en tools | TypeError en cross-process compare | `agent/tools/{cpcv,run_backtest,strategy_metrics,walk_forward,list_alerts,delete_alert}.py` |
| 5 | alerts | `_bias_listener_loop` duplica eventos con >1 worker | Escalado horizontal roto | `alerts/runtime.py:309-342` |
| 6 | market | `tests/indicators/test_panel.py` roto — 3 failed silently | Cobertura B4 regression OFF | `tests/indicators/test_panel.py:41,103,131` |
| 7 | market | `/ws/market` sin auth | DoS + docstring miente | `app/market/ws_routes.py:56-91` |
| 8 | core | WS token en query string + log de `token[:8]` | Sesiones secuestrables vía access logs | `app/market/ws_routes.py` + `core/auth/session.py:96-118` |
| 9 | setups | Rate-limit scout ignora `source='scout_proposal'` → bypass `MAX_ACTIVE_PER_SYMBOL` | Bot autónomo runaway | `scout_dispatcher.py:119,140,170` |
| 10 | reviewer | Bug indentación `setups/runtime.py:792-808` (price_move branch sobre-indentado) | Frágil; un format pass rompe la lógica | `setups/runtime.py:792-808` |

---

## Hallazgos secundarios serios (mención honorable)

- **backtest/metrics.py DSR**: usa Gumbel/EVT en lugar de Bailey closed-form. Divergencia +0.30–0.37 SR* para N ∈ [2,1000]. Rechaza estrategias buenas (conservador) pero no es la fórmula del paper. Fix con `scipy.stats.norm.ppf`.
- **setups/runtime.py**: `r_multiple = -1.0` hardcoded ignora BE move (SL post-BE = +0R, no -1R). Métricas de win-rate quedan sesgadas.
- **setups/risk_manager.py**: mal nombrado — implementa trade management (BE/trailing/time-stop), no position sizing. Sizing real está en el LLM (`agent/system_prompt.py:380-410`) sin floor/cap determinístico. Crítico para "bot autónomo".
- **agent/agent.py**: tools registry NO está en orden alfabético (contradice CLAUDE.md). Riesgo cache-invalidation Anthropic en próximo refactor inocente.
- **reviewer/dispatcher.py**: claim_review_slot atómico ✓ por construcción Postgres, pero si agent crashea entre claim y `insert_review` el setup queda "claimed sin review" 30 min. Sin rollback.
- **notifications/routes.py**: `_handle_callback` invoca `approve_setup`/`reject_setup` como funciones Python directas → bypassea Depends/middleware. Extract a `setups/service.py`.
- **market/**: layering violation — `market/ohlcv/repo.py` y `ingestion_live.py` importan `floor_to_timeframe` desde `app.agent.tools._time`. Capa de datos depende del agente.

---

## Lo que está bien (lo que NO toques)

- **Refactor estructural PR8–PR11**: imports limpios, routers todos cableados en `main.py`, sin ciclos detectables, post_mortem y reviewer extraídos con consistencia.
- **PSR Mertens variance**: fórmula correcta vs paper Bailey 2012 (verificado numéricamente).
- **No-lookahead** en backtest runner + indicators (52 tests pasando).
- **Compounding multiplicativo** real (mark-to-market sobre equity vigente).
- **Indicators**: Wilder smoothing explícito, RSI robusto, ADX con warmup correcto.
- **Approval gate atómico** en setups (`SELECT FOR UPDATE` + UNIQUE en setup_events post-021).
- **Citation validators** del agent: 3 layers (tool_name + handle existence + snapshot numeric tolerance 0.1%).
- **Bind code Telegram seguro**: secrets + GETDEL atómico + TTL + 32^6 keyspace.
- **BetterAuth wiring**: header > cookie preference correcta para cross-domain.
- **Cardinality discipline** en metrics: no user_id/symbol como label.
- **System prompt cache-friendly**: bloques ordenados, sin interpolación per-request.
- **`OpenSetupRow.source` opcional**: backward-compat sin migración blocking.

---

## Plan de fix priorizado (recomendación)

### Sprint 1 — Bloqueantes F4 (~5 días)

1. **Patrón A**: PR "multi-tenant `user_id` scoping" (journal + backtest + agent tools + paper repo + notifications UNIQUE).
2. **Patrón C**: PR "datetime UTC sweep" (12 sitios + `pydantic.AwareDatetime`).
3. **Patrón D — tests rotos**: arreglar `test_panel.py` + `test_review_observability.py` (residuos del refactor).
4. **Bug indentación**: re-indentar `setups/runtime.py:792-808`.
5. **Security**: `/ws/market` exigir auth + mover WS token a primer frame (no query string).

### Sprint 2 — Diseño del Paper Engine (~5–10 días)

6. **Diseño**: ADR para `PaperRuntime`, tablas `paper_orders`/`paper_positions`/`paper_balance`/`paper_equity_snapshots`, migración a `Decimal`, integration con `setups/runtime.py` (entry_hit → simulate_fill → persist).
7. **Implementación**: motor + tests (la suite mínima de los 11 casos del informe paper_trading.md).
8. **Position sizer determinístico**: extraer de LLM a `setups/position_sizer.py` con cap por equity/leverage/correlation.

### Sprint 3 — Concurrencia + observabilidad (~3 días)

9. **Patrón B**: arreglar claim rollback en reviewer, listener bias single-elected, race n_runs en backtest, lock por (symbol, tf) en market live.
10. **Tests** para core/auth + dispatcher post_mortem + claim concurrency.
11. **Subscribe resilient**: wrapper en `core/pubsub.py` con backoff + jitter.

### Backlog (post-F4)

- DSR Bailey closed-form (correcteness científica)
- Outbox pattern para notifications (pre-F5)
- Renombrar `risk_manager.py` → `trade_manager.py`
- Mover `floor_to_timeframe` de `agent/tools/_time` a `core/time` (layering)
- Unificar tags OpenAPI (`meta` vs `observability`)
- DRY de `_collect_tool_names` (3 copias en agent/reviewer/post_mortem)

---

## Métricas de la auditoría

- **Tiempo**: ~5 min (12 agentes paralelos)
- **Cobertura**: 100% módulos backend + tests + migraciones + wiring frontend
- **Total hallazgos**: 224 (38 críticos, 87 importantes, 99 minor)
- **Reportes**: `apps/api` tiene ~225 archivos `.py`; los 12 informes individuales viven en `/Users/samuelperez/trading/.claude/audit-2026-05-12/<modulo>.md`

---

## Próximo paso sugerido

Revisa el SUMMARY y los informes individuales. Cuando tengas claro qué fixes quieres priorizar, abre un plan de implementación (puede ser otro `/plan`) por sprint. Después de los fixes, genero la **documentación detallada por módulo** (funcionalidades, contratos, relaciones cross-módulo) en `/Users/samuelperez/trading/docs/modules/<modulo>.md` — paso pendiente, marcado como tarea #4.
