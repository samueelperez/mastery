# Audit: paper_trading/

**Auditor**: general-purpose agent
**Fecha**: 2026-05-12
**Módulo**: apps/api/app/paper_trading/
**Tamaño**: 117 (engine.py) + 124 (repo.py) = 241 LOC + 195 LOC tests

---

## TL;DR — Hallazgo dominante

**El módulo "paper_trading/" NO es un motor de paper trading.** Es únicamente:
1. Una función pura `simulate_fill` que calcula slippage adverso por fill (entry o exit).
2. Una función `compute_funding_cost_bps` para prorratear funding por hold time.
3. Un repo con `insert_paper_fill` (escribe en `paper_fills`) y `aggregate_observed_slippage_p75` (lee p75 para calibrar `SLIPPAGE_BUFFER_R`).

**No existe**: posiciones, órdenes, equity curve, balance, matching engine, mark-to-market, cierre parcial, fees aplicados a USD, ni control de margin/leverage. **No es invocado desde ningún sitio en `app/` salvo tests.** Ver bloque 🔴-1.

Si "F4 = paper trading 24/7" implica un motor real con equity, balance y posiciones, **el módulo actual cubre <10% de lo necesario**. La auditoría a continuación trata lo que existe.

---

## 🔴 Critical (BLOQUEANTE — F4 ES este módulo)

### C1. El módulo NO está cableado a producción
`/Users/samuelperez/trading/apps/api/app/paper_trading/engine.py` y `repo.py`:
- `grep -rn "simulate_fill\|insert_paper_fill\|aggregate_observed_slippage" apps/api/` → **0 callers fuera de tests**.
- `app/main.py:9-25` registra `AlertsRuntime`, `SetupRuntime`, `LiveIngestion`, pero **ningún `PaperRuntime`** ni dispatcher de fills.
- `app/setups/runtime.py:1-100` (el watcher de transitions pending→active→closed) detecta `entry_hit`, `sl_hit`, `tp_hit` pero **nunca llama** al engine ni inserta `paper_fills`. Es decir, hoy un setup `active` que toca SL cambia su `status` pero no persiste un fill simulado con slippage/fees.
- `app/setups/routes.py:10-11` declara intención ("only `approved` setups get fills simulated in the paper engine") pero ese plumbing **no existe**.

**Impacto**: ir a F4 sin escribir un `PaperRuntime` que (a) escuche cierre de candle, (b) llame `simulate_fill` en entry/exit del setup, (c) escriba `paper_fills`, (d) actualice una tabla `paper_positions` / equity curve = **F4 no funciona**.

### C2. No existe el concepto de "posición" ni "equity"
**No hay tablas** `paper_positions`, `paper_orders`, `paper_balance` ni `paper_equity_curve`. La única tabla es `paper_fills` (`alembic/versions/017_paper_fills.py:25-46`):
```
qty_pct numeric NOT NULL CHECK (qty_pct > 0 AND qty_pct <= 1)
```
**`qty_pct` es la fracción de la posición que se rellena, no la cantidad en coin ni en USD**. Con esta tabla **no puedes** computar:
- Realized PnL absoluto (necesitas `qty_coin` o `notional_usd`).
- Average cost basis al cerrar parcial (no hay history per-position).
- Equity curve (no hay balance inicial ni acumulado).
- Unrealized PnL (no hay tabla de posiciones abiertas mark-to-market).
- Fees en USD (`fee_bps` es relativo, sin notional no se traduce a $ perdido).
- Exposición concurrente / margin usage.

**Bloqueante**. Antes de F4 hay que diseñar las tablas `paper_orders`, `paper_positions`, `paper_equity_snapshots` y los repos/lógica correspondientes. Lo actual sólo sirve para post-hoc calibración de `SLIPPAGE_BUFFER_R`.

### C3. **Dinero en `float`, no en `Decimal`**
`engine.py:38-49` y `repo.py:29-37` declaran `intended_px: float`, `filled_px: float`, `qty_pct: float`, `spread_pct: float`, `atr_pct: float`, `slippage_bps: float`, `fee_bps: float`, `funding_bps: float`.

Postgres almacena como `numeric` (correcto, `017_paper_fills.py:34-41`), pero el ida-y-vuelta Python→Postgres pasa por `float`. Para slippage en bps esto suele ser tolerable, pero **cuando se calculen PnL realizados, equity y balance, todo dinero DEBE ser `Decimal` o `int` (centavos)**.

Ejemplo concreto en `engine.py:81`:
```python
filled_px = inp.intended_px * (1.0 + direction_sign * slippage_pct / 100.0)
```
Si `intended_px = 50000.0` (BTC) y `slippage_pct = 0.005` (0.005%), el resultado es `50000.0 * 1.00005 = 50002.5`, exacto. Pero `50000.000000001 * 1.0001234567890123` no será reproducible bit-a-bit, y en cierres parciales con weighted-avg cost basis acumulando floats encadenados, **diverge progresivamente del valor verdadero**.

**Acción**: para F4, migrar todo el tipo numérico de PnL/balance a `decimal.Decimal` con contexto explícito (precision 28, ROUND_HALF_EVEN). El módulo actual puede quedarse en float **solo** para slippage/fee bps (escala bps, no monetaria); para `qty_coin`, `notional_usd`, `realized_pnl_usd`, `unrealized_pnl_usd`, `balance_usd`, `equity_usd` → `Decimal` obligatorio.

### C4. Inconsistencia doc/código: `max(...)` vs `half_spread + impact`
`engine.py:6`:
```
slippage_pct = max(spread_pct, atr_pct * latency_seconds / 60 * k)
```
`engine.py:64-71` (código real):
```python
half_spread_pct = max(inp.spread_pct, 0.0) / 2.0
impact_pct = max(max(inp.atr_pct, 0.0) * (inp.latency_seconds / 60.0) * inp.impact_k, 0.0)
slippage_pct = half_spread_pct + impact_pct
```
Diferencias:
1. Doc usa `max(A,B)`, código usa `A/2 + B`. **No son equivalentes**. Si spread=0.02 y impact=0.6, doc da 0.6, código da 0.61. Si spread=0.02 e impact=0, doc da 0.02, código da 0.01.
2. Doc no menciona la división por 2 del spread.

El test `test_long_entry_pays_half_spread_when_atr_is_zero` (test_engine.py:25-39) ratifica que el código es lo que se busca (half-spread, no full-spread). Esto es **correcto financieramente** (un fill market cruza la mitad del spread desde el mid, no el spread completo).

**Acción**: actualizar docstring del módulo (`engine.py:1-25`) para reflejar la fórmula real: `slippage_pct = spread_pct/2 + atr_pct * latency_seconds/60 * k`. Es una bug-trap para futuros lectores que copien la fórmula del docstring.

### C5. Tests cubren <10% de lo que F4 necesita
`tests/paper/test_engine.py` (único archivo, 195 LOC) testea solo:
- `simulate_fill` (8 tests): half-spread direction long/short entry/exit, impact escalado, latency, no-spread baseline, monotonía no-negativa.
- `compute_funding_cost_bps` (4 tests): long paga si funding>0, short recibe, prorrateo parcial, zero.

**No existe ningún test de**:
- Long open + close → realized PnL absoluto (`r_multiple` correcto, fees deducidos, slippage acumulado entry+exit).
- Short open + close → mismo.
- **Partial fills + weighted avg cost basis** (CRÍTICO: sin esto no puedes cerrar 50% y luego el otro 50% y obtener PnL correcto).
- Fees aplicados al notional (no en bps abstractos).
- Funding aplicado al hold real (no probado contra una posición).
- Multiple positions same symbol (la app actual no soporta esto: no hay tabla).
- Edge cases: qty=0, qty<0, price=0, price<0, NaN, Inf en inputs.
- Concurrencia (dos fills simultáneos sobre mismo `trade_id`).
- Race condition (un setup que toca SL y TP en la misma vela — la lógica del setup runtime maneja esto en `runtime.py:495` con "SL prevalece" pero no hay test que verifique que ESO se traduce en fill correcto).
- Atomicidad: orden + actualización posición + escritura equity en una sola transacción.
- Negative balance / margin check.
- Aislamiento por `user_id` (no hay test que dos users con mismo trade_id se vean cruzados, lo cual debería fallar por FK pero hay que asegurar query scoping).

**Acción bloqueante para F4**: escribir la suite mínima cuando exista el engine completo. Sin esos tests no se puede dar luz verde a 24/7.

---

## 🟡 Important

### I1. `funding_bps` se calcula pero no se persiste vinculado a un hold real
`compute_funding_cost_bps(side, funding_rate_8h, hold_hours)` es una función pura sin acoplamiento con persistence. En `PaperFillRow.funding_bps: float = ...` el caller debe pasarlo, pero no hay nada que (a) muestree `funding_rate_8h` periódicamente, (b) acumule `hold_hours` desde entry, (c) calcule el running funding. Para F4 hay que agregar muestreo de funding por símbolo y persistir snapshots — sin esto, todos los `funding_bps` serán 0 (default en DB, `017_paper_fills.py:41`).

### I2. `spread_pct` y `atr_pct` son inputs sin proveedor
`grep` muestra que nadie en `app/` calcula ni provee `spread_pct` para alimentar `simulate_fill`. No hay orderbook ingestor en `app/market/`. `atr_pct` se puede derivar de `app/indicators/` pero no hay un wrapper que lo entregue al motor de fill. Para F4 hay que:
1. Suscribir a depth/orderbook L1 de Binance (websocket) para `spread_pct` en tiempo real.
2. Snapshot del spread al momento del fill.
3. Inyectarlo en `FillSimulationInput`.

Si no, el simulador siempre recibirá `spread_pct=0` (o un default constante) y subestimará costes sistemáticamente.

### I3. No hay validación de inputs en `simulate_fill`
`engine.py:52`: ninguna defensa contra:
- `intended_px <= 0` (precio cero o negativo → `filled_px` cero o negativo, propagaría a PnL desastrosos).
- `latency_seconds < 0` (filtrar primero negativos, no por `max(...)` en la línea 67-69 — eso clampa `atr_pct`, no `latency_seconds`).
- `impact_k < 0` (idem).
- `taker_fee_bps < 0` o > 100 bps (sanity bound).
- `math.isnan(intended_px)` / `math.isinf(intended_px)` → produciría NaN propagando.

Mínimo: `assert intended_px > 0`, `assert math.isfinite(intended_px)`, `assert 0 <= latency_seconds <= 600`.

### I4. La FK a `journal_trades` exige que el trade exista ANTES del fill
`017_paper_fills.py:27`:
```sql
trade_id uuid NOT NULL REFERENCES journal_trades(id) ON DELETE CASCADE
```
Buen diseño (cascade de borrado), pero el flujo F4 será: setup activo → entry_hit → simulate_fill → ¿qué trade_id? Si el `journal_trades` row se crea solo al cierre (como hoy con `log_trade` tool, `agent/tools/log_trade.py`), entonces el fill de entry no tiene trade_id válido aún.

**Acción**: o (a) crear `journal_trades` con estado "open" al entry_hit y completarlo al exit, o (b) mover `paper_fills.trade_id` a NULLable y poblarlo al cierre cuando se cree el journal row. Recomiendo (a) para que cada fill sea ACID con su trade.

### I5. `cost_fraction = funding_rate_8h * intervals` puede overflow conceptual
`engine.py:114-117`: si `hold_hours` es absurdo (e.g. 1000h) y `funding_rate_8h` es 1% (extremo), `cost_fraction = 1.25`, → cost_bps = 12500 = 125%, lo cual es semánticamente inválido (no puedes perder más del notional vía funding sin liquidación previa). No es un bug del cálculo (matemáticamente está bien), pero F4 debe verificar liquidación antes de aplicar funding y cerrar la posición.

### I6. Engine es síncrono, repo es async — fricción para llamarlos juntos
`simulate_fill` es sync (correcto, pura). `insert_paper_fill` es async (correcto, DB). El glue layer (que aún no existe) deberá llamar `simulate_fill` y luego `await insert_paper_fill(session, row)`. No es un bug, solo nota arquitectónica.

### I7. `slippage_bps` siempre positivo, pero el docstring promete poder ser negativo
`engine.py:60-62` dice "Positive bps = lost edge; negative = improvement (rare but possible when the candle's range straddles the intended price favorably)". El código (`engine.py:86-89`) admite explícitamente "By construction (both entry and exit move to worse side) slippage is always non-negative bps". **Inconsistencia**. El test `test_slippage_bps_always_non_negative` (test_engine.py:144-159) confirma que en práctica nunca es negativo. **Acción**: borrar la promesa del docstring (líneas 60-62) o implementar la mejora-de-precio cuando un fill candle-based detecte mejor precio que el intended.

---

## 🟢 Minor / cleanup

### M1. Docstring de `engine.py:24` apunta a path obsoleto
`engine.py:24`: "persistence lives in `app/storage/paper_repo.py`". Post-PR8 (commit 913d0be) movido a `app/paper_trading/repo.py`. Actualizar.

### M2. `metadata` default-empty con `json.dumps({})` en repo
`repo.py:79`: `json.dumps(row.metadata or {})`. Si `row.metadata` es `{}` (no None pero vacío), funciona; si es `None`, también. OK pero podría usar `or "{}"` para evitar la llamada a json.dumps cuando ya tienes el default `'{}'::jsonb` en DB (017:43). Micro-optimización.

### M3. `symbol.upper()` solo en INSERT, no en aggregate
`repo.py:67` upper-cases en INSERT. `repo.py:112` upper-cases en SELECT. Consistente, OK.

### M4. Magic numbers en defaults
`engine.py:41-42`: `latency_seconds=1.0`, `impact_k=0.3`, `taker_fee_bps=4.0`. Deberían vivir en `app/core/config.py` (junto a `slippage_buffer_r_btcusdt`) para tunear sin tocar código. El `taker_fee_bps=4.0` (Binance USDT-M taker) cambiará si el user usa otro exchange. Hoy queda hard-coded.

### M5. `n_fills` se castea a float en repo
`repo.py:123`: `"n_fills": float(n)` — semánticamente es int. Devolver `int` desde el dict (dejar el dict heterogéneo `dict[str, float | int]` o `dict[str, Any]`).

### M6. No hay `__init__.py` exports
`apps/api/app/paper_trading/__init__.py` está vacío (0 líneas). Importar fully-qualified es OK, pero exponer top-level (`from app.paper_trading import simulate_fill, insert_paper_fill, PaperFillRow`) ayuda y deja semántica de API pública explícita.

### M7. Sin docstring de módulo en repo.py — tiene uno corto, OK
Sin acción.

### M8. `aggregate_observed_slippage_p75` no scoping por user_id
`repo.py:99-114`: la query agrega `paper_fills` por `symbol` GLOBALMENTE. Si dos usuarios tienen estilos de trading diferentes (latencia, agresividad), su slippage observado se mezcla y la calibración resulta de la media. Para v1 single-user OK, pero **multitenant**: filtrar por `user_id` (o mantenerlo global si la calibración debe ser de mercado, no per-user — decisión de producto).

---

## ✅ Lo que está bien

- **Fórmula de half-spread** (`engine.py:65`): semánticamente correcta — un fill market cruza la mitad del spread desde el mid.
- **Direccionalidad entry/exit** (`engine.py:73-79`): `direction_sign = 1 if long else -1`, y `*= -1` en exit. Matemáticamente impecable; los tests `test_long_exit_fills_below_intended` y `test_short_exit_fills_above_intended` (test_engine.py:114-141) lo verifican.
- **Funding signo** (`engine.py:117`): long paga si funding>0, short recibe (signo invertido). Coincide con la convención Binance USDT-M y los tests `test_funding_long_pays_when_funding_positive` y `test_funding_short_receives_when_funding_positive` lo cubren.
- **Prorrateo de funding** (`engine.py:114-116`): `intervals = hold_hours / 8.0`. Correcto: en Binance USDT-M cada 8h liquida funding. 24h = 3 intervals. Test `test_funding_partial_interval_prorated` cubre 4h = 0.5 interval.
- **Pure-function engine**: sin estado, fácil de testear, fácil de paralelizar.
- **Persistence schema** (`017_paper_fills.py`): índices apropiados (`(symbol, filled_at DESC)` para calibración, `(trade_id, kind)` para reconstruir trade history), CHECK constraints en `side`, `kind`, `qty_pct`. FK con CASCADE a `journal_trades` para integridad.
- **Tests de unidad puros** son sólidos para lo poco que cubren; pinearon la fórmula con `math.isclose(rel_tol=1e-9)`.
- **PR8 consolidation limpia**: commit 913d0be solo renombra paths (`paper/engine.py` → `paper_trading/engine.py`, `storage/paper_repo.py` → `paper_trading/repo.py`). 0 huérfanos detectados (`grep` en todo `apps/api/app/` no encontró ningún path antiguo).
- **`taker_fee_bps=4.0`** corresponde a Binance USDT-M taker actual (0.04% = 4 bps). Realista.

---

## Notas adicionales

### Fórmulas verificadas

**Slippage**: `slippage_pct = spread_pct/2 + atr_pct * (latency_s / 60) * k`
- Half-spread component: 0.02% spread → 0.01% half. Test `test_long_entry_pays_half_spread_when_atr_is_zero` (test_engine.py:37): `intended=100, filled=100.01`. ✅
- Volatility impact: `atr=2%, latency=60s, k=0.3 → impact = 2 * 1.0 * 0.3 = 0.6%`. Test `test_atr_scaled_impact_adds_on_top_of_spread` (test_engine.py:71-74): `filled=100.61, slippage=61bps`. ✅
- Slippage en bps: `slippage_pct * 100`. Para `slippage_pct=0.01` (0.01%), bps=1. Coherente (1bps = 0.01% = 1/10000 = 1 unit-of-bps).

**Funding**: `cost_bps = funding_rate_8h * (hold_hours/8) * 10000 * (long ? +1 : -1)`
- `funding=0.0001 (0.01%), hold=24h → 3 intervals → 0.0003 fraction → 3 bps`. Test `test_funding_long_pays_when_funding_positive` (test_engine.py:172). ✅
- Convención de signo (long paga si rate>0): correcta para Binance USDT-M.

### Casos NO cubiertos en tests pero relevantes para F4

| Caso | Estado | Bloqueante para F4? |
|---|---|---|
| Long open + close PnL absoluto | ❌ no testeado | SÍ |
| Short open + close PnL absoluto | ❌ no testeado | SÍ |
| Partial close + weighted avg cost basis | ❌ no testeado | SÍ (mayor riesgo de bug) |
| Fees aplicados al notional (USD) | ❌ no testeado | SÍ |
| Funding aplicado a hold real con muestreo | ❌ no testeado | SÍ |
| Multiple posiciones mismo símbolo | ❌ no soportado (sin tabla) | SÍ |
| Concurrencia (2 fills simultáneos) | ❌ no testeado | SÍ |
| Atomicidad orden+pos+equity | ❌ no implementado | SÍ |
| Aislamiento por user_id | ❌ no testeado, query global | SÍ multitenant |
| Inputs negativos / NaN / Inf | ❌ sin guard | SÍ defensa en profundidad |
| Slippage bps negativo (mejora) | Promete docs, no implementado | NO bloqueante |

### Recomendación pre-F4

Antes de declarar F4 listo, escribir un **diseño** que defina:
1. Tablas `paper_orders`, `paper_positions`, `paper_balance`, `paper_equity_snapshots`.
2. Un `PaperRuntime` task lifespan-managed (patrón `SetupRuntime`/`AlertsRuntime`) que escuche cierres de candle y consuma transitions de setup → fills.
3. Tipos: `Decimal` para todo lo monetario.
4. Suite de tests mínima (los 11 casos del cuadro arriba).
5. Migración orderbook L1 ingestor (para `spread_pct` real) o asumir un `default_spread_bps` por símbolo configurable.
6. Funding sampler periódico (cada 8h por símbolo).
7. Estrategia de aislamiento per-user_id en todas las queries y semaforización por user para fills concurrentes.

El módulo actual es **buen building block para slippage simulation y calibración** pero **NO es un motor de paper trading completo**. F4 requerirá 5-10x más código + tests.
