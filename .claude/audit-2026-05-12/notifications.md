# Audit: notifications/

**Auditor**: general-purpose agent
**Fecha**: 2026-05-12
**Módulo**: apps/api/app/notifications/

## Mapa rápido

- `notifications_router` montado en `app/main.py:21,85`.
- Importadores externos: solo `app/setups/scout_dispatcher.py:400-401` (lazy import dentro de try/except, fire-and-forget). Reviewer, alerts, paper_trading, setups runtime NO mandan notifs todavía.
- Endpoints expuestos: `POST /notifications/telegram/bind-code`, `GET /notifications/telegram/status`, `DELETE /notifications/telegram`, `POST /telegram/webhook`.

---

## 🔴 Critical

### 1. Webhook llama a `approve_setup`/`reject_setup` como funciones Python directas — bypassea integridad del flujo HTTP (routes.py:233-241)
```python
from app.setups.routes import approve_setup, reject_setup
await approve_setup(setup_id, user_id)
```
Esto invoca el handler de FastAPI como función pura. Funciona hoy porque ambos handlers solo dependen de parámetros (`trade_id`, `user_id`). Pero:
- **Acoplamiento frágil**: si mañana `approve_setup` añade un `Depends(...)` extra (e.g. rate-limit, audit middleware), la llamada desde Telegram se rompe en silencio (los `Depends` no se resuelven cuando llamas la función directamente — el parámetro queda con su valor por defecto, que es el `Depends(...)` object, generando un TypeError o validación incorrecta).
- **Security boundary saltada**: si en el futuro se añade CSRF/origin checking, queda outside.
- Mejor: extraer la lógica de negocio a `app/setups/service.py::approve(trade_id, user_id)` y llamarla desde ambos sitios (HTTP route + Telegram callback). El handler HTTP se queda como cáscara fina.

### 2. `_resolve_user_from_chat` no valida que el chat_id sea único — race condition de re-bind permite hijacking (routes.py:289-307, repo.py:18-33)
- `set_telegram_chat_id` upsert por `user_id`, pero **no hay UNIQUE index sobre `telegram_chat_id`**. Si user A binda chat_X, luego user B obtiene su propio bind code y por error (o adversarialmente: si conoce el bind code de A) hace `/start` desde chat_X, la fila de B queda con `telegram_chat_id = chat_X`. El `_resolve_user_from_chat` ahora puede devolver A o B según el `LIMIT 1` (no determinista).
- **Mitigación**: añadir `UNIQUE INDEX user_notification_settings_chat_id_idx ON user_notification_settings(telegram_chat_id) WHERE telegram_chat_id IS NOT NULL` y, en `set_telegram_chat_id`, antes del upsert hacer `UPDATE … SET telegram_chat_id = NULL WHERE telegram_chat_id = :chat_id AND user_id != :uid` para desvincular dueño previo (o lanzar 409).
- **Severidad**: alta porque al pulsar Approve/Reject desde un chat, `_resolve_user_from_chat` decide qué user_id se usa para `approve_setup`. Si el lookup devuelve el user equivocado, una persona puede aprobar setups de otro usuario.

### 3. `unbind_telegram` deja huérfano el `chat_id` en Telegram — siguiente bind code expirado del mismo chat re-vinculará automáticamente (bind.py + routes.py:86-93)
- Tras `DELETE /notifications/telegram`, la fila DB pierde el chat_id, pero Telegram sigue viendo al bot en su lista. Si el user manda `/start <CODIGO>` con un código válido (incluso de otro user, por algún error), se re-vincula. Esto es esperado, **pero** no hay un `/stop` que limpie también el lado Telegram (que el bot deje de responder). Aceptable para v1 pero documentar.
- Más relevante: cuando un user desvincula, NO se le manda un mensaje Telegram confirmando el unbind. El user sigue pensando que está vinculado.

---

## 🟡 Important

### 4. Fire-and-forget sin outbox: si Valkey/Telegram caen, el setup llega al user sin notificación y nadie lo sabe (scout_dispatcher.py:395-414)
- El comentario en `telegram.py:14` confirma el patrón: "Telegram outage NEVER crashes the scout dispatcher". Eso está bien para disponibilidad, pero **no hay retry queue**: si `send_setup_alert` devuelve False, el setup queda persistido sin haberse notificado, y el user pierde la oportunidad. La métrica `mt_telegram_sends_total{outcome=http_error|transport_error}` solo permite ver el síntoma agregado.
- Pre-F4 (paper trading) es aceptable; pero antes de F5 (live execution) hay que: (a) outbox pattern (tabla `pending_notifications` + worker que reintenta con backoff) o (b) NOTIFY canal pubsub que cualquier UI conectada también consume.

### 5. `_resolve_bot_username` cache global no thread-safe + no expira (routes.py:258-286)
- `_bot_username_cache` es una variable de módulo, mutada sin lock. Bajo asyncio en uvicorn worker único es seguro, pero rompe en deploys multi-worker (cada worker hace su propio getMe, no es bug pero es ruido en logs).
- Más importante: si el bot username cambia (raro pero posible: ops cambia de bot, mismo token nuevo), la cache nunca se invalida hasta restart. Mover a `functools.lru_cache` async-compat o usar Valkey con TTL de 1h.

### 6. `_handle_callback` no valida que el setup pertenece al user del chat_id (routes.py:223-241)
- `_resolve_user_from_chat` da `user_id`, luego `approve_setup(setup_id, user_id)`. La validación de propiedad la hace el handler HTTP (`WHERE id = :tid AND user_id = :uid`), así que técnicamente funciona — devuelve 404 si no es del user. Pero el path actual depende de que TODOS los callers internos respeten el guard. Si en el futuro se añade un `approve_setup_internal` que asume validado, esto se rompe.
- Recomendación: validar explícitamente en el webhook handler antes de llamar (`SELECT 1 FROM journal_trades WHERE id = :tid AND user_id = :uid`) para defense-in-depth.

### 7. Webhook devuelve 200 incluso si el `_process_update` falla (routes.py:128-136) — correcto para Telegram retries, pero oculta bugs en logs
- Si `_process_update` lanza, se loguea y se devuelve 200. Esto evita retry-storms (bien), pero no incrementa ninguna métrica de error. Añadir `telegram_webhook_errors_total{kind}` para alertabilidad ops.

### 8. `send_text` NO escapea ni usa `parse_mode` (telegram.py:203-206)
- Los mensajes de bind flow (routes.py:169, 178, 187, 196, 213, 218, 225, 238, 241, 243) están en español con tildes y comillas backtick `` ` `` — Telegram en texto plano los renderiza tal cual, **pero el char ` (backtick) en un mensaje sin parse_mode se renderiza como backtick literal**, no como código. Cosmético. Más serio: si el `exc.detail` que llega desde HTTPException trae caracteres especiales o algo controlable por el user, no hay escaping. Hoy `exc.detail` viene de los handlers internos (strings constantes), así que safe — pero frágil contractualmente.

### 9. `set_telegram_chat_id` upsert no escribe `created_at` explícito — depende de DEFAULT (repo.py:18-33)
- Cuando hace UPDATE branch, `created_at` se preserva (correcto). Cuando hace INSERT, `created_at` toma DEFAULT now() (correcto). Bien. Pero el `INSERT … VALUES` omite `created_at` — el DEFAULT se aplica solo en INSERT, no en UPDATE. **Verificado**: no es bug. Sin acción, anotado por completitud.

---

## 🟢 Minor / cleanup

### 10. `bind.py:33` calcula `safe` en cada llamada — moverlo a constante de módulo
```python
safe = "".join(c for c in _CODE_ALPHABET if c not in {"O", "0", "I", "1"})
```
Inocuo (32 chars), pero `_SAFE_ALPHABET = ...` como constante es más legible.

### 11. `bind.py:53` "3 collisions in a row" en mensaje pero `for _ in range(3)` — coincide. Nada que arreglar; el comentario en docstring del módulo dice "retry once" (línea 13), inconsistencia con código. Aclarar a "retries up to 3".

### 12. `telegram.py:106-110` `_escape_md` no soporta texto que ya contenga `\` válido — caso límite (e.g. un `summary_es` con un `\n` literal en el texto del idea, no como newline). Probabilidad baja porque summary viene del LLM en prosa natural; documentar como known limitation.

### 13. Magic numbers sin nombre:
- `telegram.py:62,275` timeout `10.0` segundos — extraer a `Settings.telegram_http_timeout_seconds`.
- `telegram.py:150` `summary_es[:280]` — extraer a constante `_TELEGRAM_SUMMARY_MAX_CHARS`.
- `bind.py:45` retry `range(3)` — constante `_MAX_CODE_COLLISION_RETRIES`.

### 14. `routes.py:107` returns `{"ok": "true"}` (string "true") en lugar de `{"ok": True}` (bool). Telegram no le importa pero es inconsistente.

### 15. `_handle_message` no logea cuando llega un mensaje desconocido (línea 196: "gentle nudge" sale, pero no hay log.info con el chat_id para auditar uso). Útil para ops para entender comportamiento del bot.

### 16. `telegram.py` importa `TradeIdea` de `app.agent.models` — acoplamiento con dominio del agent. Aceptable hoy; si se añaden más notification types (alerts, reviews), considerar un `NotificationPayload` interface en `notifications/`.

---

## ✅ Lo que está bien

- **Bind code seguro**: `secrets.choice`, 6 chars de alfabeto 32 (sin O/0/I/1) = 32^6 ≈ 1.07B combinaciones; TTL configurable; `SET NX` previene colisiones; `GETDEL` atómico en consume — bind code es one-time, no enumerable, expira (bind.py:45-68). Cumple los 3 criterios del checklist Security.
- **Webhook secret con `compare_digest`** (routes.py:121-125) — timing-safe; mensaje 401 no diferencia missing vs wrong (no leaks).
- **TELEGRAM_BOT_TOKEN nunca se logea**: revisión exhaustiva confirma que solo aparece en URLs internas (`_api_base`, `_resolve_bot_username`) y nunca como campo de log structlog. `resp.text[:200]` en `telegram.py:72` podría leakear si Telegram devolviera el token en error body, pero Telegram nunca lo hace.
- **Degradación graceful**: si `TELEGRAM_BOT_TOKEN` falta, todos los endpoints + el scout dispatcher retornan/loguean en lugar de crashear. Métrica `outcome=no_token` permite alertabilidad.
- **MarkdownV2 escape** propio (`_escape_md`) en lugar de pull-in de markdown lib — minimalista y cubre los chars que el escape reservado de Telegram exige; tests cubren `.` y `!`.
- **Mensaje setup_alert NO incluye balances/API keys/tokens**: `format_setup_alert` solo emite direction, symbol, TF, entry, SL, TPs, confidence, regime, summary. Sizing y leverage NO se publican (bien — son privados; `position_size_pct` y `leverage_x` están en TradeIdea pero el formatter los ignora deliberadamente). Cumple el criterio Security "NO incluir balances exactos sin opt-in".
- **Métricas Prometheus por outcome** (`mt_telegram_sends_total{method, outcome}`) — observable.
- **Tests pure-function bien cubiertos**: `_fmt_price`, `_escape_md`, `format_setup_alert`, `_inline_kb_for_setup` con el caso de 64-byte callback_data. Cubre regresiones de formato.

---

## Notas adicionales

### Gaps de test
- `bind.issue_bind_code` / `consume_bind_code`: ningún test cubre el flow Redis (TTL, GETDEL atómico, colisiones). Necesita fakeredis o un Valkey de test. **Importante** porque el bind flow es el principal vector de seguridad.
- `_process_update` / `_handle_message` / `_handle_callback`: no testeados (requieren fixtures de payload Telegram + mock de `tg.send_text`). Cobertura de los happy paths + edge cases (código expirado, callback con `data` malformado, `compare_digest` con secret incorrecto).
- `repo.set_telegram_chat_id` / `get_telegram_chat_id` / `unbind_telegram`: no testeados a nivel DB (requiere pytest-postgresql o test container). Mínimo: round-trip upsert + lookup.
- `format_setup_alert` con `summary_es` que contenga chars MarkdownV2 reservados (`. ! _ * [`) — verificar que el escape se aplica al texto del summary, no solo al wrapper.
- Webhook integration: secret válido + `/start <code>` válido → chat_id persistido. Hoy "exercised in smoke tests with a real bot token" (comentario en test file) — pero CI no los corre.

### Dependencias / acoplamientos
- `notifications/` depende de: `core.config`, `core.db`, `core.broadcasting.pubsub` (Valkey), `core.auth`, `core.observability.metrics`, `agent.models.TradeIdea`, `setups.routes` (las funciones approve/reject — ver Critical #1).
- Acoplamiento sano excepto el direct-import de `setups.routes` en el callback handler.

### Pre-F4 readiness
- **Listo para F4** con los Critical #1 y #2 resueltos (extraer `setups/service.py` + UNIQUE index sobre chat_id). #3 y los Important pueden ir en backlog F4.1.
- **Pre-F5** (live execution): el outbox pattern (#4) es bloqueante — perder una notificación de un live trade es serio.
