# Audit: journal/

**Auditor**: general-purpose agent
**Fecha**: 2026-05-12
**Módulo**: apps/api/app/journal/ (bias_detector, embeddings, repo, routes, summary)

---

## 🔴 Critical

### C1. `get_by_id` y `list_all_for_embed_check` NO scope por `user_id` — leak cross-tenant
**`apps/api/app/journal/repo.py:181-195`** (`get_by_id`) y **`:225-248`** (`list_all_for_embed_check`).

```python
# repo.py:189
FROM journal_trades WHERE id = CAST(:id AS uuid)
# repo.py:237-243 — sin filtro user_id
SELECT ... FROM journal_trades ORDER BY trade_ts ASC LIMIT :lim
```

CLAUDE.md dice "All DB writes scope by `user_id`". `get_by_id` permite que un usuario lea trade de otro si conoce el UUID. Peor: `scripts/embed_backfill.py:29` lo invoca con `batch_size=1000` y vuelve a embedar **trades de TODOS los usuarios mezclados**, escribiendo `update_summary_and_embedding` que tampoco filtra por user (`repo.py:152-173`, sólo filtra por `id`). Es un cross-user worker silencioso.

Fix: añadir `user_id` arg requerido a ambas funciones y AND `user_id = :uid` en SQL; backfill debe iterar por usuario.

### C2. `_HYBRID_SQL` filtra dense+bm25 por user_id, pero el `LEFT JOIN setup_post_mortems` NO comprueba ownership del post-mortem
**`repo.py:336`** — `LEFT JOIN setup_post_mortems pm ON pm.trade_id = t.id`.

Si por error de FK / mezcla histórica un `setup_post_mortems.trade_id` apuntara a un trade de otro usuario (o si en F4 se introduce trade re-asignación), el pm.verdict/lesson_es se filtraría. Recomendable AND `pm.user_id = :uid` defensivo aunque hoy debiera ser redundante.

### C3. Retry de Voyage atrapa `Exception` genérico — máscara errores 4xx programáticos
**`embeddings.py:55`** — `retry=retry_if_exception_type((Exception,))`.

Esto reintenta también 400 (texto malformado), 401 (key inválida) y 413 (payload too large). Con `wait_exponential_jitter` puede tardar ~14s antes de propagar un error que es inmediato. Voyage SDK expone `voyageai.error.RateLimitError`, `voyageai.error.APIError`. Filtrar a 429/5xx, o como mínimo excluir `AuthenticationError` / `InvalidRequestError`.

### C4. No timeout explícito en llamada Voyage
**`embeddings.py:78-83`** — `await client.embed(...)` sin `timeout=`. El SDK de voyageai usa httpx por defecto (timeout largo o `None`). Bajo carga el `log_trade` tool puede colgar la request del agent indefinidamente; combinado con C3, esto se amplifica con retries. Pasar `timeout=10` (param soportado por `voyageai.AsyncClient`).

---

## 🟡 Important

### I1. `summary_text` NOT NULL en migración, pero **no se valida** longitud ≤300 en el path Python
**`repo.py:41` (JournalTradeIn.summary_text: str)** — no `max_length`. Migración 002 columna `text` sin CHECK. CLAUDE.md dice "`summary_text` (300-char truncation) stays for listings". El único punto donde se trunca es `setups/repo.py:94` (`idea.summary_es[:300]` via slicing), pero `agent/tools/log_trade.py:42-52 → build_summary_text` puede producir output >300 si `mistakes` o `news_24h` son largos. No hay validación. Sugerido: `Field(..., max_length=300)` en `JournalTradeIn` o explicitar el contrato en doc.

### I2. `summary_es_full` no se persiste desde `journal/` (sólo setups/repo.py); journal/ ignora F5
**`journal/repo.py:21-44` (JournalTradeIn)** carece de `summary_es_full`, `confluences`, `scenarios`. Solo `setups/repo.py:201-256` los escribe via INSERT directo en `journal_trades`. Esto significa que `tools/log_trade` (que sí pasa por `JournalTradeIn`) **nunca** poblará F5 — los trades manuales/csv import quedan sin tesis verbatim. Si F4 paper trading delega en `setups/repo.py` está OK; si delega en `journal/repo.insert_trade`, los paper trades pierden la tesis y el reviewer falla el guard `dispatcher.py:379`.

### I3. `embed_batch` no chunk-ea — silenciosamente fallará en batches >128
**`embeddings.py:69`** documenta "voyage's batch limit" pero no aplica. `scripts/embed_backfill.py:58` ya pasa `BATCH = 16`, así que en práctica está OK, pero un caller futuro que pase `len(texts) > 128` recibirá un error de Voyage (no de nuestra capa). Mejor: assert defensivo o auto-chunk con warning.

### I4. `embeddings.py` no valida que `len(result.embeddings[i]) == EMBEDDING_DIM`
Si Voyage cambia el default Matryoshka o el SDK ignora `output_dimension` (versión vieja), insertaríamos vectores con dim != 1024 → asyncpg lanzaría error en INSERT, pero el mensaje sería opaco. Añadir un check post-embed: `assert all(len(v) == EMBEDDING_DIM for v in embeddings)`.

### I5. `_vector_literal` usa `f"{x:.7g}"` — precisión limitada
**`repo.py:76`** — `.7g` da ~7 sigfigs, suficiente para cosine en 1024-d pero borderline. pgvector acepta texto con full precision; podríamos usar `.9g` o `repr()`. No es un bug ahora (Voyage devuelve floats ya cuantizados), pero documentarlo evita sorpresas si migramos a embeddings binarizados.

### I6. `_fetch_recent_trades` excluye `paper`/`live` para "no spam-ar con bot trades" — pero F4 (paper trading) introducirá esos modos y la detección de bias quedará ciega
**`bias_detector.py:53-63`** — `AND mode IN ('manual_log', 'csv_import')`. CLAUDE.md F4 viene a continuación. Documentado, pero conviene crear un issue: ¿bias detection sobre paper trades? Probablemente sí cuando el agente sea autónomo (state autonomous_bot.md).

### I7. `bias_events` no se borra cuando se borra `journal_trades` (no FK)
**Migración 002:73-83**. Si se eliminan trades de un usuario, los bias_events quedan huérfanos. Añadir FK con `ON DELETE CASCADE` o limpiar por user_id en cleanup paths.

### I8. Concurrencia en `run_for_user` — DELETE + INSERT no es atómico
**`bias_detector.py:330-358`**. Dos workers ejecutando `run_for_user(uid, lookback=30)` simultáneamente pueden ambos borrar y luego ambos insertar → duplicados. No hay unique constraint en `(user_id, window_start, window_end)`. Probable: NIGHTLY job único, pero si se llama desde tool `detect_bias_patterns` ad-hoc + cron simultáneo, race posible.

---

## 🟢 Minor / cleanup

### M1. `EMBEDDING_DIM = 1024` magic number
Centralizado en `embeddings.py:25`, pero la migración 002 lo replica como `vector(1024)` literal. Si cambias uno y olvidas el otro, asyncpg falla al INSERT. Comentar el coupling en la migración (ya hay un comment en `__init__.py`; refuerza en migration).

### M2. `_retrying()` retry_if_exception_type((Exception,)) es equivalente a "retry siempre" — la tuple no añade nada
Pasar `retry=retry_if_exception_type(voyageai.error.RateLimitError)` (ver C3) o usar `retry_if_not_exception_type(...)`. Tal como está, semánticamente engañoso.

### M3. `hash_summary` normaliza whitespace pero no normaliza unicode (NFC/NFD)
**`summary.py:66`** — `" ".join(text.split())`. Si `mistakes` contiene "é" en NFC vs NFD (clipboard de macOS suele NFD), el hash cambia y se re-embed sin necesidad. Bajo impacto; añadir `unicodedata.normalize("NFC", text)` antes del split.

### M4. `summary.py:46` — el head se construye con un `f"{...}".strip()` final pero no protege contra dobles espacios cuando algunos campos son vacíos
Si `tf=""` o `side=""`, queda `"BTCUSDT  long"` (doble espacio). Cosmético — afecta BM25 tokens muy levemente. Fix: `" ".join(filter(None, [...]))`.

### M5. `_detect_revenge` y otros detectores usan magic numbers (1.2×, 1.5×, 0.5×, 15 min) sin constantes nombradas
**`bias_detector.py:135, 209, 253`**. Si quieres calibrar por usuario en F2.5 (blueprint §7.4 "calibrated to your own history"), ahora es hard-coded global. Sugerido: pasar como kwargs o leer de `Settings`.

### M6. `_detect_fomo` está marcado como stub pero ya se persiste — puede ensuciar la UI
**`bias_detector.py:271-299`** — flagea por setup_tag ∈ {unknown, impulse, fomo, ""}, lo cual es trivialmente cierto si el agente nunca asigna setup_tag en F2. Riesgo: spam de bias events FOMO. Sugerido: gate behind feature flag o esperar F2.5 features.atr_at_entry.

### M7. `routes.py` tiene tags inconsistentes (`tags=["research"]` vs `tags=["journal"]` vs `tags=["journal", "monitoring"]`)
Cosmetic en OpenAPI grouping.

### M8. `routes.py:75-127` ejecuta SQL inline en lugar de delegar al repo
Hay endpoints en routes.py que repiten queries que ya existen en repo.py (list_recent vs list_journal_trades, get_by_id vs get_journal_trade). Mantenibilidad: extraer al repo y unificar el shape de columnas devueltas.

### M9. `_hit_from_row` filtrado de factor_verdicts ignora `verdict='neutral'`
**`repo.py:386-393`** — documenta que descarta neutral, pero no hay test que lo verifique.

### M10. Dead code potencial: `bulk_insert` (`repo.py:126-131`)
No tiene callers (grep cero) y es un loop trivial sobre `insert_trade`. Si no se usa, remover.

### M11. `embeddings.py:101` — `close()` usa `getattr(_client, "aclose", None) or getattr(_client, "close", None)` pero NO se llama en `app/main.py` lifespan
Conexiones httpx quedan colgando hasta GC. Doc explícito ("Wire to FastAPI lifespan if desired") pero nunca cableado. En staging probablemente OK; en Railway puede acumular.

---

## ✅ Lo que está bien

- **Imports tras refactor**: `journal/__init__.py` exporta API limpia (`build_summary_text`, `embed_batch`, `embed_one`, `hash_summary`, `EMBEDDING_DIM`, `BiasFlag`, `run_for_user`). Todos los callers (`agent/tools/biases.py`, `agent/tools/similar_setups.py`, `agent/tools/journal_query.py`, `agent/tools/log_trade.py`) usan paths correctos post-refactor.
- **journal_router montado** correctamente: `main.py:17` import + `:82` `app.include_router(journal_router)`.
- **HNSW index** correcto para pgvector con `vector_cosine_ops` (migración 002:59-61).
- **CAS update** (`update_summary_and_embedding` con `expected_old_hash`) protege contra race entre `embed_backfill` y `log_trade` (`repo.py:152-173`).
- **`_dedup_hash` + idempotencia en `run_for_user`** (DELETE-then-INSERT por ventana) — re-runs del nightly no duplican (aunque ver I8).
- **VOYAGE_API_KEY no leakea en logs**: `embed_batch` solo loguea `total_tokens` (`embeddings.py:85-91`); el `_get_client()` no incluye la key en el RuntimeError.
- **`require_user_id`** dependency aplicado consistentemente en `routes.py` para los endpoints expuestos.
- **summary_text orden de campos correcto** (setup_tag + regime primero — blueprint research finding confirmado en test `test_summary_orders_discriminative_fields_first`).
- **summary.py truncamiento es char-level**, no byte-level; con `slice` Python opera sobre code points. No hay riesgo UTF-8.
- **`thinking`-low + 7 tools en reviewer** sigue el patrón de CLAUDE.md sin tocar journal/.

---

## Notas adicionales

### Cobertura de tests
- **tests/journal/** sólo cubre `bias_detector` (4 detectores; falta `_detect_fomo`) y `summary` (4 tests).
- **Cero tests** para:
  - `embeddings.py` (mockear voyageai, validar retry, validar dim del vector).
  - `repo.py` (insert/update CAS, `hybrid_search`, `_hit_from_row` con factor_verdicts).
  - `routes.py` (auth scope, 404 paths, holdout drift_warning).
- **F5 (summary_es_full / confluences / scenarios)** sin tests directos en journal/; los tests viven en `tests/runtime/test_review_prompt_thesis.py` para el reviewer prompt, no para el journal layer.
- Falta test para C1 (cross-user leak en `get_by_id`).

### Acoplamiento con otros módulos
- `routes.py` importa de `backtest/`, `post_mortem/`, `reviewer/`, `setups/`. El journal router actúa como **agregador read-side** de varios dominios — está OK pero hace que el módulo `journal/` no sea autónomo (no podrías arrancar la API sin esos módulos). Considera mover los endpoints `/journal/setups`, `/journal/reviews`, `/journal/post-mortems`, `/journal/holdout-performance` a sus dominios respectivos para que `journal/routes.py` solo tenga `/journal/trades*`.

### Acción recomendada antes de F4
1. Fix C1 (cross-user leak) — bloqueante.
2. Decidir si paper trades se persisten via `journal.repo.insert_trade` o `setups.repo.insert_setup_from_idea`. Si el primero, ampliar `JournalTradeIn` con F5 fields (I2).
3. Añadir tests para embeddings (mock voyageai) y repo (in-memory pgvector o testcontainer).
4. Filtrar retries de Voyage a tipos transitorios (C3) + timeout (C4).
