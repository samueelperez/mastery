# Audit: post_mortem/

**Auditor**: general-purpose agent
**Fecha**: 2026-05-12
**Módulo**: apps/api/app/post_mortem/

Módulo extraído de `agent/` en PR9. Hermano paralelo de `reviewer/`. Disparado por `setups/runtime.py` cuando un setup cierra (SL hit o TP-all hit). Persiste en `setup_post_mortems` (migración 012, limpiada en 015).

---

## 🔴 Critical

Ninguno. Tras PR9 el módulo está integralmente cableado: dispatcher invocado fire-and-forget desde `setups/runtime.py:122-134` y `:522`/`:580`, repo expuesto vía routes en `journal/routes.py:397-438` (montadas vía `journal_router` en `main.py:82`), config flags presentes en `core/config.py:100-103`, migración 012 + 015 aplicadas, y existen tests de validators + de `_cluster_lessons`. No hay imports residuales `from app.agent.<x>` que debieran ser `from app.post_mortem.<x>`.

(Los tres ítems "no-test" para dispatcher/repo no se marcan Critical porque dispatcher por defecto opera con `post_mortem_enabled=False` — riesgo de "feature dormida sin probar" pero no rompe producción hoy. Ver Important #1.)

---

## 🟡 Important

### I1. `dispatcher.py` no tiene tests (es el componente más complejo del módulo)

- Existen tests de validators (`tests/agent/test_post_mortem_validators.py`) y de `_cluster_lessons` (`tests/storage/test_recurring_lessons.py`), pero **ninguno cubre**: `_compute_mfe_mae` (long/short asymmetry, `r_unit<=0` edge, ventana sin candles), `_compute_entry_vs_exit_delta` (regime_changed flag, snapshot None, scorer raises), `_build_factor_verdicts` (semantic_tags merge), `_outcome_from_r` (los thresholds 0.2R), `_extract_usage_and_cost` (chargeable_input math).
- Tampoco existe un test integration end-to-end del dispatcher con `post_mortem_enabled=True`. Antes de activar el flag en producción esto es riesgo: una regresión silenciosa devuelve `None` en `maybe_run_post_mortem` (porque el catch-all en líneas 83-91 silencia todo) y no se entera nadie.
- **Recomendación**: añadir `tests/post_mortem/` con al menos `test_mfe_mae.py`, `test_outcome_from_r.py`, `test_dispatcher_idempotency.py` (mockear el agent.run y verificar ON CONFLICT path), y `test_factor_verdicts.py`.

### I2. `summary_es = pm.lesson_es` colapsa dos campos distintos
- `dispatcher.py:219`: `summary_es=pm.lesson_es`. El comentario dice "summary == lesson para post-mortem v1", pero `summary_es` es NOT NULL en migración 012 (línea 76) — escribir la misma cadena en ambos garantiza que cuando se quiera diferenciar (resumen narrativo vs lección accionable) requerirá migración o backfill.
- Si la intención es que sean iguales para siempre, lo correcto es dropear `summary_es` (igual que se dropearon `what_worked`/`what_failed` en 015). Mantener dos columnas con valor idéntico es la deuda que migración 015 vino a corregir.

### I3. `trigger_kind: str` vs `TriggerKind` literal — typing leak
- `dispatcher.py:60`: `trigger_kind: str  # 'setup_closed_sl' | 'setup_closed_tp'`. El reviewer hermano usa `TriggerKind = Literal[...]` (definido en `agent/models.py:373` e incluye `setup_closed_sl`/`setup_closed_tp` precisamente para esto — comentario en migración 014).
- `setups/runtime.py:522`/`:580` pasa el literal hardcoded, así que no hay bug runtime, pero `mypy --strict` no protege contra typos futuros. Cambiar firma a `TriggerKind` y los validators del Literal hacen el resto.

### I4. `settings.exchange` no existe en `Settings` — defensiva inútil
- `dispatcher.py:128`: `exchange=settings.exchange if hasattr(settings, "exchange") else "binance_usdm"`
- `dispatcher.py:157`: `exchange=getattr(settings, "exchange", "binance_usdm")`
- `core/config.py` no define `exchange`. La fuente canónica es `app.core.exchanges.binance_adapter.EXCHANGE_NAME` (usada en `setups/runtime.py:48,960`). El `hasattr`/`getattr` siempre cae al default — dead defensive code. Reemplazar por `from app.core.exchanges.binance_adapter import EXCHANGE_NAME` y usar `exchange=EXCHANGE_NAME` directo.

### I5. `outcome='partial_win'` nunca se genera
- Migración 012:60 define outcome ∈ {win, loss, breakeven, partial_win}. La CHECK constraint lo acepta.
- `_outcome_from_r` (`dispatcher.py:492-500`) solo retorna `win`/`breakeven`/`loss`. `partial_win` está definido para `exit_reason='manual_close' con r>0 sin todos los TPs` (migración 012 docstring línea 28), pero **el dispatcher solo es invocado con `setup_closed_sl`/`setup_closed_tp`** — manual close nunca llega. La rama `partial_win` es código documentado pero inalcanzable.
- O bien añadir trigger para manual_close (y ajustar `_exit_reason_from_trigger` para retornar `'manual_close'`), o eliminar `partial_win` de la CHECK constraint para no engañar a quien lea el schema. Antes de F4 (paper trading) hay que decidir.

---

## 🟢 Minor / cleanup

### M1. Triple duplicación de `_collect_tool_names`
- `agent/validators.py:70`, `reviewer/validators.py:22`, `post_mortem/validators.py:24`: idéntica. PR9 era el momento natural para sacarla a helper compartido — `app.agent.validators._collect_tool_names` es público de facto.
- Sugerencia: mover a `app/agent/validators_shared.py` (o `app/agent/_validator_utils.py`) e importar desde los tres sitios. Bajo riesgo, alto retorno legibilidad.

### M2. `summary_es=pm.lesson_es` ya cubierto en I2 (no duplicar fix)

### M3. `_normalize_row` (repo.py:203) no incluye `factor_verdicts` en jsonb_keys
- Línea 211: `("factor_verdicts", "entry_vs_exit_delta", "citations", "usage_tokens")`. Sí está. Pero `targets` y otros JSONB de `journal_trades` no — irrelevante aquí, el row es de `setup_post_mortems`. OK; era falsa alarma. (Mantener para historial de revisión.)

### M4. `_compute_mfe_mae` itera dos veces sobre candles
- `dispatcher.py:341-344`: `max()`, `min()`, luego `next(c.ts for c in candles if float(c.h) == max_high)` — recorre la lista otra vez. Para 5000 candles es ms, pero un solo pass guardando `(ts, h, l)` mientras se acumula es la forma idiomática.
- Borderline: optimización prematura para esta carga. Marcado solo por completitud.

### M5. `agent.py:38` magic string `POST_MORTEM_MODEL_ID = "anthropic/claude-sonnet-4.6"` duplica el del reviewer
- Reviewer hermano hace lo mismo (`reviewer/agent.py:35`). Idealmente vendría de `Settings.review_model_id` (o equivalente) con default, no de constante en cada módulo. Si OpenRouter cambia el slug, dos sitios que tocar.

### M6. `_lesson_fingerprint` lista de stopwords hardcoded (~30 palabras)
- `repo.py:250-307`. Comentario admite que es heurística. Aceptable para v1 (preamble building debe ser cheap), pero documentar el ADR — si se sube a embedding-based clustering, este código muere completo.

### M7. Settings no expone pricing post_mortem específico
- `dispatcher.py:663-665`: usa `review_price_*_per_m_usd` para el post-mortem ("reutiliza pricing flags configurados para review"). Comentario lo justifica (mismo modelo) — válido por ahora pero si se sube/baja `thinking` para uno y no para el otro, el cost telemetry se desvía. Considerar `post_mortem_price_*` alias del review en config (low effort).

### M8. `factor_verdicts` no graba "neutral" verdicts cuando una key del snapshot no aparece en success/failure
- `_build_factor_verdicts` (`dispatcher.py:435-489`) marca como "neutral" si la key no está en ninguna lista. Bien. Pero los **semantic_tags** que aparecen en el snapshot pero no son citados por el agente también caen en "neutral" — esto es correcto, solo apunto que el behavior contractual de "neutral = agente vio la key y eligió no atribuir" se mezcla con "agente nunca pensó en esta key". Sin consecuencias prácticas para v1.

### M9. `dispatcher.py:286` `import contextlib` inside function
- Microcoste de import (cached tras 1ª llamada), pero por convención los imports van al top del módulo. Mover al top.

---

## ✅ Lo que está bien

- **Idempotencia bien resuelta**: UNIQUE(trade_id) + ON CONFLICT DO NOTHING + chequeo del `inserted is None` en repo (`repo.py:105-107`) evita audit events duplicados en race condition. Mejor que cooldown frágil — apropiado para evento terminal.
- **Pre-cómputo determinístico antes del LLM**: `_compute_mfe_mae` y `_compute_entry_vs_exit_delta` corren ANTES de `agent.run()`. El agente recibe los números, no los deriva. Esto es exactamente la filosofía del blueprint (LLM = interpreter, no oracle).
- **MFE/MAE se persiste a `journal_trades.mfe_mae` aunque el agente falle** (`dispatcher.py:139-150`). Datos determinísticos sobreviven la indisponibilidad del LLM. Bien pensado.
- **Banned tools list** (`validators.py:37-42`) explícitamente prohíbe citar `get_multi_tf_confluence` (circular — está auditándolo) y mutadores. Razón documentada inline. Soft gates de coherencia `thesis_held → success_factors no vacío`, etc. (`validators.py:114-135`).
- **Reusa canal Valkey `reviews:user:{user_id}`** con `type='post_mortem'` (`dispatcher.py:240`). Frontend ya escucha — no requiere subscriber nuevo. Pragmático.
- **Feature flag default-off** (`config.py:100`: `POST_MORTEM_ENABLED=False`). Rollout gradual posible (data layer primero, agente después) sin riesgo a producción.
- **System prompt cache-friendly**: ~140 líneas, FROZEN, version-tagged (`POST_MORTEM_SYSTEM_PROMPT_VERSION="pm1"`). Todo per-request context va en user message. Cumple lo que dicta `CLAUDE.md`.
- **Migración 015 limpieza propia**: `what_worked`/`what_failed` removidas con justificación documentada — el equipo identificó y arregló su propia duplicación. Buena señal.
- **Tests de `_cluster_lessons` y `_lesson_fingerprint`** son robustos: cubren accents/case, stopwords, order invariance, top_k, sample_symbols cap. Bien.

---

## Notas adicionales (consistencia con reviewer/)

| Aspecto | reviewer/ | post_mortem/ | Consistente? |
|---|---|---|---|
| Modelo | `anthropic/claude-sonnet-4.6` | idem | sí |
| `thinking` | `"low"` | `"medium"` | distinto, justificado (contrafactuales) |
| `max_tokens` | 8000 | 6000 | distinto, justificado (PostMortem schema bounded) |
| Semáforo global | `_concurrency_sem` lazy | `_concurrency_sem` lazy | sí |
| Lazy singleton | sí | sí | sí |
| Channel Valkey | `reviews:user:{user_id}` | reusa el mismo | sí (intencional) |
| Catch-all error log | `review.failed` | `post_mortem.failed` | sí |
| `_collect_tool_names` | duplicado | duplicado | inconsistente (DRY) — ver M1 |
| Trigger kind type | `TriggerKind` literal | `str` | inconsistente — ver I3 |
| Pricing config | `review_price_*` | reusa review's | inconsistente — ver M7 |
| Cooldown | atomic `claim_review_slot` | sin cooldown (terminal) | distinto, justificado |
| Banned citation tools | sin lista | sí (`_BANNED_CITATION_TOOLS`) | distinto, justificado |
| Tests | sí (validators + repo) | sí (validators + _cluster) | sí (pero falta dispatcher — ver I1) |
| Reuses `setup_events.event='review_generated'` | sí | sí | sí (compartido intencional, comentado en repo.py:49-50) |

**Veredicto consistencia**: post_mortem/ sigue el patrón de reviewer/ con desviaciones todas justificadas en código. Las 3 inconsistencias accionables (M1 duplicación helper, I3 typing, M7 pricing) son refactors low-effort para PR12.

**Antes de F4 (paper trading)**: priorizar I1 (tests dispatcher) e I5 (resolver `partial_win` muerto). I2 (`summary_es == lesson_es`) e I4 (`settings.exchange`) son housekeeping del extract — deuda menor.
