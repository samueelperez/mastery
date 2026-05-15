# Audit: setups/

**Auditor**: general-purpose agent
**Fecha**: 2026-05-12
**Módulo**: `apps/api/app/setups/` (PR8–PR11 post-refactor, pre-F4)

Alcance: `repo.py` (1067 ln), `risk_manager.py` (418 ln), `routes.py` (194 ln), `runtime.py` (1116 ln), `scout_dispatcher.py` (422 ln). Migraciones 005, 008, 010, 016, 020, 021. Tests: `tests/runtime/test_risk_manager.py`, `test_setup_runtime_conditions.py`, `test_scout_approval_gate.py`, `tests/integration/test_scout_smoke.py`.

---

## 🔴 Critical

### 1. Rate-limit del scout NO cuenta sus propios proposals — bypass de hard cap
**`scout_dispatcher.py:119,140,170`**
Migración 020 introdujo `source='scout_proposal'` distinto de `'agent_proposal'`. Las tres queries de rate-limit/dedup quedaron pinned al filtro antiguo:

```python
WHERE user_id = :uid
  AND source = 'agent_proposal'   # ← debería incluir scout_proposal
  AND symbol = :sym
  AND status IN ('pending', 'active')
```

- `_count_active_setups_for_symbol` (`MAX_ACTIVE_PER_SYMBOL=3`) → scout puede acumular N proposals/símbolo sin tope.
- `_count_proposals_in_last_24h` (`MAX_PROPOSALS_PER_DAY=10`) → cap de 10/día se aplica a chat-iniciados; scout es ilimitado.
- `_find_similar_open_setup` → dedup ATR-based ignora scout-vs-scout duplicates.

**Impacto F4**: scout autónomo 24/7 puede generar runaway proposals si el régimen activa muchos rules sin que existan setups del chat. Es el modo *exacto* en que la dirección "bot autónomo" del proyecto opera. Fix: `source IN ('agent_proposal', 'scout_proposal')` en las tres queries — exactamente como ya hace `list_setups` (repo.py:434) y `count_setups_by_status` (repo.py:460) para counts del journal.

### 2. SL/TP fills no modelan gap-through — irreal para F4 paper-trading
**`runtime.py:501–512`**
Cuando `_sl_hit` matches, `exit_px = setup.stop_loss_px` y `r_multiple = -1.0` se hardcodean. Si el bar entero está por debajo de SL (gap-down en long), el sistema asume fill exacto en SL. Idéntico para TP en línea 569 (`last_price = float(last_hit_t["price"])`).

Realidad: stop-market fills at bar.open (peor que SL en gap-through); flash crashes fillan a bar.low. Para F4 paper-trading que será comparado con live, esto sobreestima win rate y subestima drawdown. El módulo `paper_trading/engine.py::simulate_fill` ya tiene el modelo de slippage; debería invocarse aquí. Hoy `engine.py` es código muerto en esta ruta (no es importado por `setups/`).

### 3. `risk_manager.py` no es position sizing — es trade management
El audit pide "position sizing math (Kelly? volatility-targeted?)". El módulo se llama `risk_manager.py` pero implementa **BE move + trailing + time stop**. El sizing real está en `agent/system_prompt.py:380–410` (`position_size_pct = risk_per_trade_pct / stop_distance_pct × 100`) — lo calcula el **LLM** y lo valida `agent/validators.py:1132`. NO hay sizing determinístico aquí.

**Impacto F4**: el LLM puede emitir `position_size_pct` inconsistente turno a turno, y no hay floor/ceiling determinístico ni cap por correlation/portfolio exposure. Para "bot autónomo" el sizing debe ser del sistema, no del modelo. Plan: extraer un `position_sizer.py` con la fórmula explícita + cap por equity, leverage, correlation. Rename `risk_manager.py` → `trade_manager.py` para precisión.

### 4. `_evaluate_close` carrera entre invalidation y entry_hit en mismo tick
**`runtime.py:641–724`**
Step 1 evalúa invalidations cuyo `spec.(symbol,tf)` matchea el tick actual. Step 2 evalúa entry/SL/TP del propio (symbol, tf) del setup, **filtrando `s.id not in invalidated_ids`**. Pero un setup cuya invalidation_condition vive en `(BTCUSDT, 4h)` y cuyo propio (symbol, tf) es `(SOLUSDT, 1h)` se invalidaría en el tick de `BTC@4h` — y en paralelo el tick de `SOL@1h` (otro market_loop task) podría estar evaluando entry_hit sobre la copia stale leída en su propio `list_open_setups`.

Cada market_loop es una task asyncio independiente con su propia `list_open_setups()`. La única defensa es el `SELECT ... FOR UPDATE` añadido en `runtime.py:430` que detecta status≠pending. **Funciona** porque la invalidation hace `UPDATE ... status='cancelled'`; el segundo task ve status≠pending y aborta. Pero está expresado como audit fix solo del race "manual reject"; el comentario en `routes.py:158–161` no enumera el race cross-task de invalidation. Recomendado: agregar comentario explícito + un test que cubra el escenario cross-symbol/cross-tf simultaneous.

### 5. Indentation visualmente sospechosa en `_evaluate_price_review_triggers`
**`runtime.py:792–808`**
Las líneas 793–808 están indentadas con **20 espacios** (12 base + 8 extra) dentro de `if threshold_crossed and far_enough_from_last:`. Python lo acepta (un solo bloque), pero es un *legacy from a wrapped if* y será trampa al editar. Re-indentar a 16 espacios. Mismo bloque tiene `continue` que sale del `for setup in actives:` — correcto en runtime, pero confuso visualmente.

---

## 🟡 Important

### 6. `_check_expiry_and_invalidate` no protegido con `SELECT ... FOR UPDATE`
**`runtime.py:201–227`**
A diferencia del entry_hit (`runtime.py:429–447` añadió lock explícito), la rama de expiry-wall-clock hace `UPDATE WHERE id=:tid AND status='pending'` directo. La idempotencia vía `WHERE status='pending'` evita doble-cancel — bien. PERO si entre tasks concurrentes uno evalúa expiry y otro evalúa entry_hit en el mismo setup en el mismo tick, el último UPDATE gana sin lock y `setup_events` puede tener dos rows `invalidated` + `entry_hit` con orden no determinista. Probabilidad baja (mismo wall-clock + mismo candle) pero el sweeper periódico (`_expiry_sweep_once`) corre cada 60s independiente.

### 7. `r_multiple = -1.0` hardcoded en SL hit ignora BE move
**`runtime.py:511`**
Si el setup pasó por BE move (SL = entry_px), un SL hit posterior es **+0R** (breakeven), no `-1R`. El código siempre escribe -1. `_classify_outcome` (repo.py:541) entonces marca como `loss`. Tras BE move el outcome debe re-derivarse: `r = _r_multiple(side, entry, original_sl_at_propose, exit_px)`, no `-1`. El SL "original" no está persistido — la migración 016 añadió `risk_state` pero no preserva el `entry_sl`. **Fix**: persistir `original_stop_loss_px` al transicionar entry_hit, o calcular outcome real al cierre desde `targets[0].hit_at`.

### 8. `_setup_summary_text` usa `summary_es[:300]` — pierde tesis para journal
**`repo.py:84–95` + `repo.py:241`**
Migración 010 introduce `summary_es_full` para preservar la tesis completa, pero `summary_text` sigue siendo 300-char + decoración. Si el front-end del journal listing usa `summary_text` (no `summary_es_full`), seguimos en la situación pre-010. Verificable en `journal/routes.py`.

### 9. `update_targets_hits` re-inserta `tp_hit` event para parcial sin distinguir
**`repo.py:842–853`**
El evento siempre es `'tp_hit'` regardless de "parcial vs final". El payload tiene `label/price` pero el evento no distingue de `transition_status(... event='tp_hit')` en cierre final. Frontend que filtre por `event='tp_hit'` no diferencia. Sugerido: `event='tp_partial'` (necesita CHECK constraint update).

### 10. `_apply_risk_manager` evalúa BE/trailing con `close`, SL check usa `low/high`
**`runtime.py:266–376` + `runtime.py:501`**
RiskManager corre sobre `close`. Si BE mueve SL a entry y el mismo bar tiene `low ≤ entry`, el SL fires *inmediatamente* en el mismo tick. Esto es "BE instantáneo" — comportamiento defendible pero contraintuitivo. El sistema persiste BE move + SL hit como dos eventos en la misma transacción (líneas 330–337 + 502–512). Cada uno en su propio `session_scope`, no atómicos. Si crash entre los dos: BE registrado, SL no. Recomendado: evaluar BE move solo cuando el bar entero esté por encima del nuevo SL (`low > new_sl`).

### 11. `_fire_review` y `_fire_post_mortem` son fire-and-forget sin retry
**`runtime.py:86–134`**
Si Valkey está caído o el DB falla en el dispatcher, la review/post-mortem se pierde silenciosamente (errores van a log.warning dentro de `maybe_run_review`). Para "bot autónomo 24/7" recomendable: cola persistente (e.g. tabla `pending_reviews` con worker), o al menos retry exponencial.

### 12. `_fetch_review_meta` abre nueva session fuera de la del caller
**`runtime.py:834–863`**
`_evaluate_price_review_triggers` llama `list_open_setups` con session A, luego `_fetch_review_meta` abre session B para fetch metadata. Doble roundtrip en cada candle close. Puede unirse al SELECT inicial vía LEFT JOIN.

### 13. `compute_panel_for_specs` solo se llama si `affected_pending`
**`runtime.py:655–667`**
Si TODOS los setups pendientes son del propio (symbol, tf) del tick (la mayoría), `affected_pending` queda vacío y panel NO se construye. Bien para perf. PERO: si un setup tiene invalidation_condition apuntando al propio (symbol, tf) del setup, esta no se evalúa en step 1 (filtro `spec.symbol == sym AND spec.tf == timeframe` lo incluye), pero step 2 invoca `_evaluate_setup` que NO re-evalúa condiciones — la nota del docstring (líneas 388–395) dice que las condiciones del propio TF "quedan a cargo del caller". Caller = step 1. Si el setup propio_tf == tick_tf, step 1 LO INCLUYE, ok. Mismo case-by-case verificable, pero merece test específico (no encontré test que cubra invalidation cuyo spec.tf == setup.tf).

### 14. `OpenSetupRow.invalidation_conditions=[]` hardcoded en scheduler
**`runtime.py:903`**
`_review_scheduler_once` construye `OpenSetupRow` con `invalidation_conditions=[]` siempre — para reviews time-based esto es ok (no usado en el dispatcher), pero si alguien re-usa el setup para otra evaluación se perdería data. Defensivo, no destructivo. Documentable.

---

## 🟢 Minor / cleanup

### 15. `repo.py` mezcla concerns
`repo.py` (1067 ln) tiene: inserts, lists, transitions, **fan-out a `factor_outcomes`** (líneas 556–663), **classify_outcome**, **compute_is_holdout**, **winrate aggregate**. Las últimas tres son lógica de F5.5 backtest/journal, no setups. Candidato a split: `setups/repo.py` solo CRUD + transitions; `setups/outcomes.py` para fan-out + classify.

### 16. `from sqlalchemy import text as _text` local import
**`runtime.py:838`**
`text` ya importado top-level. Limpieza.

### 17. `datetime.utcnow()` deprecado
**`runtime.py:979`**
Fallback cuando `ts` no es str. Usar `datetime.now(tz=UTC)`.

### 18. Magic numbers
- `_REVIEW_SCHEDULE_INTERVAL_S = 300.0` (runtime.py:871) — sin env override.
- `_EXPIRY_SWEEP_INTERVAL_S = 60.0` (runtime.py:1021) — idem.
- `MAX_ACTIVE_PER_SYMBOL = 3`, `MAX_PROPOSALS_PER_DAY = 10`, `DEDUP_ATR_MULTIPLE = 2.0` (scout_dispatcher.py:62–64) — module constants, no env. Si F4 quiere modo "conservador" (1/5/3) no es configurable.
- `bucket = max(abs(entry) * 0.005, 1e-6)` (repo.py:76) — 0.5% sin nombrar constante.
- `0.005` umbral oscilación en `far_enough_from_last` (runtime.py:790) — magic.

### 19. Comentarios docstring desactualizados
**`runtime.py:1011–1018`** — bloque `Lifecycle owner` aparece duplicado (uno como comentario divider antes de `_expiry_sweep`, otro antes de `SetupRuntime`). Residuo de refactor PR.

### 20. `_review_scheduler_once` parsea row defensivamente
**`runtime.py:884–915`** — try/except envuelve construcción de `OpenSetupRow` con catch genérico + log.warning. Si la query devuelve shape inválida la mejor señal es failing test, no silencio. `except (TypeError, ValueError, KeyError)` mínimo.

### 21. `targets_update` mutación in-place
**`runtime.py:533–541`** — itera `targets = list(setup.targets)` (copia) pero hace `t["hit_at"] = ...` mutando los dicts originales (la copia es shallow). Funcionalmente OK porque `OpenSetupRow.targets` viene fresh de DB en cada tick, pero patrón peligroso si alguien añade caching.

### 22. `_format_thesis_block` duplicado en `reviewer/dispatcher.py` y `post_mortem/dispatcher.py`
Mismo helper renderizando tesis, dos copias. Candidato a `app/agent/thesis_format.py`.

---

## ✅ Lo que está bien

1. **Naming `stop_loss` post-008 es consistente** end-to-end: `TradeIdea.stop_loss`, `journal_trades.stop_loss_px`, `OpenSetupRow.stop_loss_px`, `_sl_hit`, `risk_state.trailing_sl`. Migración 008 hizo el rename limpio. `invalidation` queda reservado solo para `invalidation_conditions` (pre-entry) — sin confusiones en el código revisado.
2. **Approval gate atómico** (`routes.py:162–172` + `runtime.py:429–447`): `SELECT ... FOR UPDATE` dentro de la misma transacción del UPDATE cierra el race manual-reject ↔ runtime-activate. UNIQUE index `setup_events_unique_user_decision` (migración 021) previene doble approve/reject incluso bajo race entre web + Telegram.
3. **`transition_to_invalidated` idempotente** via `WHERE status='pending'` guard. `rowcount==0` → no-op. Bien.
4. **RiskManager pure function design** (`risk_manager.py:114–205`): `compute_risk_actions` es pure, testeada exhaustivamente en `test_risk_manager.py` (484 ln). BE move idempotency via `risk_state.breakeven_moved`, trailing solo ratchet tighter, time stop terminal y short-circuits. Bien diseñado para el alcance "trade management" (el problema es el nombre — ver Critical #3).
5. **ATR fetch warm-up guard** (`risk_manager.py:395`): `if len(rows) < length: return None` evita usar valores Wilder pre-saturation.
6. **F5.5 fan-out factor_outcomes idempotente** vía `ON CONFLICT (trade_id, factor_name, factor_tf) DO NOTHING`.
7. **Cross-symbol/cross-tf invalidation conditions** soportado correctamente: `_evaluate_close` step 1 (líneas 641–693) computa panel del (symbol, tf) de la condición, no del setup. Permite "long en SOL@1h invalidado por BTC@4h close < X".
8. **Dispatcher fire-and-forget con task tracking** (`_REVIEW_TASKS` set + `add_done_callback`) — evita GC prematuro sin leak.
9. **Lifespan owner**: `main.py:42–55` arranca/para `SetupRuntime` en orden correcto (setups antes que alerts antes que ingestion al stop). `start()` es idempotente (`if self._tasks: return`).
10. **`OpenSetupRow.source` opcional con default "agent_proposal"** — backward-compat con filas pre-020 sin migración blocking.
11. **Test coverage del approval gate**: `test_scout_approval_gate.py` cubre 4 paths (no approval, with approval, agent_proposal bypass, observed-cancelled race). `test_scout_smoke.py` ejerce end-to-end contra Postgres real con cleanup determinista.

---

## Notas adicionales

### Risk math formula (lo que SÍ está y lo que NO)

**Está**:
- `compute_unrealized_r` correcto para long/short, divide-by-zero safe (`risk_manager.py:94–104`).
- `_r_multiple` para outcome en cierre — mismo patrón (`runtime.py:167`).
- ATR-trailing offset = `atr × multiple` con ratchet-only (`risk_manager.py:182–203`).
- BE threshold check sobre `close`, no `high/low` — defendible (close es la señal canónica del candle).
- TimeStop usa wall-clock `(candle_ts - entry_hit_at) >= timedelta(hours=max_hold)`, max_hold per-TF configurable.

**NO está** (gap para F4):
- **Position sizing fórmula** — solo en LLM/system_prompt. No hay módulo `position_sizer.py` determinístico.
- **Portfolio-level risk cap** — nada agregado, p.ej. "no más de 3% equity at risk simultáneo".
- **Correlation-aware cap** — el sistema permite N longs en altcoins correlados sin penalizar.
- **Slippage modeling on fills** — `paper_trading/engine.py` existe pero nadie lo llama desde `setups/`.
- **Realistic SL fill** — siempre fill at SL price, nunca gap-through.
- **R recomputation post-BE** — siempre `-1R` en SL hit, ignora si SL ya se movió a BE.

### Naming consistency F4 (audit gotcha del prompt)

Verificado: `TradeIdea.stop_loss` ↔ `journal_trades.stop_loss_px` ↔ `OpenSetupRow.stop_loss_px` ↔ `setup.stop_loss_px` en runtime ↔ `Insert: idea.stop_loss → stop_loss_px` (repo.py:243). **Consistente**. Solo queda confusión semántica con `KeyLevel.kind="invalidation"` en `agent/models.py:180` (concept distinto — anclaje gráfico) y el field `invalidation_conditions` (pre-entry rules) — pero ambos tienen docstrings que lo aclaran.

### Integridad estructural post-refactor

- `setups/` importa de: `agent.models` (TradeIdea, TriggerKind), `alerts.dsl/evaluator/panel_service/cooldown`, `reviewer.dispatcher/repo`, `post_mortem.dispatcher`, `paper_trading` → ✗ (no importa), `core.*`, `market.*`, `notifications.repo+telegram` (lazy import).
- Importadores de `setups`: `main.py` (routes+runtime), `journal/routes.py`, `reviewer/dispatcher.py`, `agent/validators.py`, `alerts/runtime.py` (lazy), `notifications/routes.py` (lazy).
- Sin ciclos detectables. Lazy imports en alerts→setups y notifications→setups bien justificados.
- `scout_dispatcher.py` es el único punto donde `setups/` invoca al `agent.agent.get_agent()` — coupling intencional para que scout reuse el agente principal.

### Tests gap para F4

- ❌ Sin test del rate-limit fix (cuando se aplique Critical #1).
- ❌ Sin test del SL gap-through (Critical #2) — requiere mock OHLCV con bar.low << SL.
- ❌ Sin test del cross-task race invalidation ↔ entry_hit (Critical #4).
- ❌ Sin test del `tp_partial` event vs `tp_hit` final (Important #9).
- ❌ Sin test del `r_multiple` post-BE move (Important #7).
- ❌ `test_setup_runtime_conditions.py` solo cubre `_parse_conditions` + DSL evaluator pura — no cubre `_evaluate_close` end-to-end con expiry+conditions+entry_hit secuenciados.
- ✅ `test_risk_manager.py` 484 ln, exhaustivo para pure logic. Falta integration con `apply_risk_action_to_db` (transacción + idempotency real).
- ✅ `test_scout_smoke.py` cubre approval gate vs real DB.
