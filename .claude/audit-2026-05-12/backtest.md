# Audit: backtest/

**Auditor**: general-purpose agent
**Fecha**: 2026-05-12
**Módulo**: apps/api/app/backtest/
**Ámbito**: post-refactor PR8–PR11, pre F4 (paper trading 24/7)

---

## Resumen ejecutivo

El refactor estructural está limpio (imports, routers en `main.py`, ningún huérfano detectado). El motor de simulación y la familia López de Prado pasan los tests existentes y la **fórmula PSR-Mertens es correcta** contra el paper (verificada numéricamente, ver "Fórmulas verificadas" abajo).

Los hallazgos críticos son **dos**:

1. La aproximación `E[max SR]` usada en DSR es **EVT/Gumbel**, no la closed-form de Bailey 2014 — **deflación sistemáticamente más severa** (~0.3-0.4 unidades de Sharpe arriba del valor del paper para N ∈ [2, 1000]).
2. `backtest_runs` y `strategy_metrics` **no tienen `user_id`** — backtests son globales. Si en F4 un agente por usuario corre sweeps de parámetros, el contador `n_runs` (que alimenta la deflación DSR de TODOS los users) se infla cross-tenant.

Nada bloquea F4 si F4 sigue siendo single-tenant. Si F4 abre a varios usuarios concurrentes en paper trading + run_backtest desde el agente, ambos puntos se vuelven bloqueantes.

---

## 🔴 Critical (bloqueante para F4 multi-tenant)

### C1. DSR `E[max SR]` usa Gumbel/EVT en lugar de Bailey closed-form
[/Users/samuelperez/trading/apps/api/app/backtest/metrics.py:240-243]

```python
ln_n = math.log(max(n_trials, 2))
sr_benchmark = math.sqrt(2 * ln_n) - (1 - 0.5772156649) / math.sqrt(2 * ln_n)
```

Bailey & López de Prado (2014) §3 define el benchmark como:
```
E[max SR] ≈ (1-γ)·Φ⁻¹(1 - 1/N) + γ·Φ⁻¹(1 - 1/(N·e))
```

La aproximación Gumbel/EVT (la que tiene el código) es asintóticamente equivalente pero **sistemáticamente más alta** que la closed-form de Bailey. Verificación numérica:

| N        | Código (Gumbel) | Bailey closed-form | Δ      |
|----------|-----------------|--------------------|--------|
| 2        | 0.8183          | 0.5198             | +0.30  |
| 10       | 1.9490          | 1.5746             | +0.37  |
| 100      | 2.8955          | 2.5306             | +0.36  |
| 1000     | 3.6032          | 3.2551             | +0.35  |

**Impacto**: para una estrategia con Sharpe per-trade 1.5 y `n_trials=100`, el benchmark del código (2.90) supera el Sharpe real → DSR ≪ 0.5 → flag `overfit_warning=True`. Bailey closed-form daría 2.53 → DSR cerca de 0.5 → no flagged. El código rechaza estrategias buenas (falsos positivos de overfit), no acepta malas. Conservador pero divergente del paper.

**Fix**: reemplazar por `scipy.stats.norm.ppf` (ya hay `scipy` en deps por `factor_stats_repo`):
```python
from scipy.stats import norm
gamma = 0.5772156649
sr_benchmark = (1-gamma) * norm.ppf(1 - 1/n_trials) + gamma * norm.ppf(1 - 1/(n_trials * math.e))
```
Añadir test que pin-eee la fórmula a la del paper (test actual `test_dsr_more_pessimistic_than_psr_when_many_trials` solo valida monotonía, no el valor exacto).

### C2. `backtest_runs` y `strategy_metrics` no tienen `user_id`
[/Users/samuelperez/trading/apps/api/alembic/versions/002_journal_and_backtests.py:93-130]
[/Users/samuelperez/trading/apps/api/app/backtest/runner.py:311-360]
[/Users/samuelperez/trading/apps/api/app/backtest/routes_backtests.py:65-123]

A diferencia de `journal_trades` (line 32: `user_id text NOT NULL DEFAULT 'me'`) y `bias_events` (line 75), las tablas `backtest_runs` y `strategy_metrics` no llevan `user_id`. La tool `run_backtest` (`tools/run_backtest.py`) y la ruta `GET /backtests` (routes_backtests.py:67) tampoco filtran por usuario.

Implicaciones:
- **DSR cross-tenant leak**: `runner.py:291-298` lee `n_runs FROM strategy_metrics WHERE strategy_id = :sid` sin user_id. Si el usuario A corre 50 sweeps de `ema_cross_atr_stop`, el siguiente backtest del usuario B usa `n_trials=51` → su DSR queda penalizado por experimentos de A.
- **Privacy leak** en `GET /backtests`: cualquier user autenticado ve runs de cualquier otro.
- **`best_dsr` corrompido** (line 355): `GREATEST(best_dsr, EXCLUDED.best_dsr)` es global, no por usuario.

**Fix**: migración que añada `user_id TEXT NOT NULL DEFAULT 'me'` a ambas tablas; renombrar PK de `strategy_metrics` a `(strategy_id, user_id)`; filtrar la `SELECT` de n_runs y la list-view por user_id desde `RunContext.deps.user_id`.

### C3. Race condition en lectura/actualización de `n_runs` para DSR
[/Users/samuelperez/trading/apps/api/app/backtest/runner.py:290-307]

```python
n_trials_row = await session.execute("SELECT COALESCE(n_runs, 0) ...")
prev_runs = n_trials_row.scalar() or 0
n_trials_for_dsr = prev_runs + 1
# ... compute_metrics (uses n_trials_for_dsr)
# ... INSERT into strategy_metrics ON CONFLICT DO UPDATE n_runs = n_runs + 1
```

El SELECT no tiene FOR UPDATE ni serializable isolation. Dos backtests concurrentes (probable en F4 con el agente 24/7) leerán el mismo `prev_runs=N`, ambos calcularán `n_trials=N+1` para su DSR, y luego ambos upserts incrementarán → `n_runs` queda en `N+2` pero ambos usaron deflación con `N+1`. Off-by-one en la deflación. Menor pero acumula bajo carga.

**Fix**: o `SELECT ... FOR UPDATE` en la misma transacción, o calcular DSR DESPUÉS del UPSERT usando `RETURNING n_runs`.

---

## 🟡 Important

### I1. `cpcv.py` hardcodea `np.sqrt(252)` para anualizar test-fold Sharpes
[/Users/samuelperez/trading/apps/api/app/backtest/cpcv.py:109-110]

```python
s_test = float(test_rets.mean() / test_rets.std(ddof=1) * np.sqrt(252))
s_train = float(train_rets.mean() / train_rets.std(ddof=1) * np.sqrt(252))
```

`run_cpcv` acepta cualquier timeframe del `base_spec` (15m/1h/4h/1d). Para 1h, los bars/año son 8760 → factor de anualización √8760 ≈ 93.6, no √252 ≈ 15.9. La `sharpe_distribution` reportada en `CPCVResult` está mis-escalada por un factor ~6× para 1h y ~25× para 15m.

Esto no afecta a `deflated_sharpe` del `CPCVResult` porque ese viene de `compute_metrics()` que sí usa `annualization_factor_for(timeframe)` (line 137). Pero el `sharpe_mean/p25/p50/p75` que se cita al usuario es incorrecto en timeframes intradía.

**Fix**: usar `np.sqrt(_BARS_PER_YEAR[base_spec.timeframe])` o pasar `annualization_factor_for(base_spec.timeframe)`.

### I2. Walk-forward: trades abiertos en warm-up filtran PnL unrealized al OOS
[/Users/samuelperez/trading/apps/api/app/backtest/walk_forward.py:126-131]

```python
purged_trades = [t for t in result.trades if t.entry_ts >= oos_start]
oos_curve = [(ts, eq) for (ts, eq) in result.equity_curve if ts >= oos_start]
```

Si un trade entra en el warm-up (`entry_ts < oos_start`) y sigue abierto al cruzar `oos_start`, su `entry_ts` lo excluye de `purged_trades` (correcto) pero su PnL **unrealized** está dentro del `equity_curve` desde el primer bar del OOS — la renormalización con `scale = initial / base_eq` lo re-encoder pero el trade nunca aparece en la lista. Cuando ese trade cierra dentro del OOS, hay un salto en la curva sin trade en el journal.

**Impacto**: métricas OOS por fold pueden tener PnL sin trade asociado → `expectancy_R` y `n_trades` divergen ligeramente del equity. Caso de borde, probable que afecte 1-2 trades por fold.

**Fix**: o descartar también los trades cuyo `entry_ts < oos_start` (incluyendo cierre durante OOS) re-simulando con stop_loss desde `oos_start`, o más simple: ignorar la primera porción del fold OOS hasta que no haya open_pos heredada.

### I3. PBO en `cpcv.py` queda `None` permanentemente
[/Users/samuelperez/trading/apps/api/app/backtest/cpcv.py:1-20, 124-128]

El docstring es explícito y honesto: el módulo se llama `cpcv.py` pero NO implementa CPCV real (re-ejecución por fold). El PBO se deja `None` porque con sólo 2 ranks por fold (train vs test) el cálculo es trivial. Esto es **correctitud por sobrenombrar**, pero `probability_of_overfit` queda como código muerto hasta F-stat-quant y `overfit_warning` se decide sólo desde DSR (line 142-144).

El `StrategyMetrics.probability_of_overfit: float | None = None` (metrics.py:71) confirma que el campo nunca se popula. La tool del agente `cpcv.py` (`agent/tools/cpcv.py`) probablemente lo expone como `null` — verificar que el system prompt no instruya al agente a citarlo.

**Acción**: dejar el TODO bien señalizado (ya está) y NO citar PBO en outputs hasta CPCV real. Está bien documentado por ahora.

### I4. CPU-bound work corre en el event loop (no thread offload)
[/Users/samuelperez/trading/apps/api/app/backtest/runner.py:264-282]

`_simulate` itera sobre todos los bars en Python puro (line 147 onwards), polars compute para indicadores (`.collect()`), `compute_metrics()` numpy heavy. Todo en el event loop async. Para una sola request es OK (segundos para 10k bars), pero F4 con 24/7 paper trading + agente disparando `run_backtest` ad-hoc bloqueará el loop durante el cálculo, congelando WS de OHLCV y el `AlertsRuntime`.

**Fix**: envolver `_simulate` + `compute_metrics` en `await asyncio.to_thread(...)`. CPCV y walk-forward son aún más costosos (N folds × `run_backtest`).

### I5. `compute_metrics()` con `equity_curve=[]` retorna `probabilistic_sharpe=0.5` pero `deflated_sharpe=0.0`
[/Users/samuelperez/trading/apps/api/app/backtest/metrics.py:282-289]

Inconsistencia menor: la rama "no data" deja PSR=0.5 (neutral por convención) pero DSR=0.0 (false-positive de overfit). Después `overfit_warning=True`. Para los casos N<3 (line 213) y trade_rets<3 (line 322-326) la convención también difiere: PSR=0.5, DSR=0.0. El test `test_compute_metrics_includes_required_fields` no toca este caso.

**Fix**: si PSR=0.5 entonces DSR deflated por n_trials=1 debería seguir siendo 0.5 (porque sr_benchmark=0 cuando n_trials==1). Cuando hay 0 trades es razonable que `overfit_warning=True` (sin evidencia), pero DSR=0.0 ≠ DSR neutral.

---

## 🟢 Minor / cleanup

### M1. `factor_stats_repo.py` vive en `backtest/` pero su lógica es journal/agent
[/Users/samuelperez/trading/apps/api/app/backtest/factor_stats_repo.py:1-50]

24k LOC dentro de `backtest/` cuyo dominio real es "Bayesian win-rate de factores observados en trades cerrados". Lo importan `journal/routes.py`, `agent/validators.py`, `agent/routes.py`, `setups/repo.py`, `agent/tools/factor_stats.py`. La única conexión con backtest es semántica (es "stats agregadas"). Considerar mover a `app/factor_stats/` o `app/journal/factor_stats.py` en un PR futuro de modularización. No bloqueante.

### M2. Tests de `factor_stats_repo` viven en `tests/storage/` y `tests/agent/`
[/Users/samuelperez/trading/apps/api/tests/storage/test_factor_stats_bayesian.py]
[/Users/samuelperez/trading/apps/api/tests/agent/test_factor_gate.py]

No hay tests en `tests/backtest/` para `factor_stats_repo` aunque el módulo está en `app/backtest/`. Consecuencia natural de M1.

### M3. `metrics.py:170-194` aproxima `capital_at_entry` con `initial_equity`
[/Users/samuelperez/trading/apps/api/app/backtest/metrics.py:170-194]

Para PSR/DSR el ratio `pnl/initial_equity` es OK por homogeneidad de escala (la media y la std del retorno se ven afectadas por el mismo factor, el ratio mean/std queda invariante). El docstring lo explica. Pero los tests no validan que esto coincida con el equity-curve real bajo compounding — `test_simulate_compounds_real_buy_and_hold` valida el motor pero no el efecto en PSR. Riesgo bajo.

### M4. `cpcv.py:79` condición arbitraria `n_folds * 5`
[/Users/samuelperez/trading/apps/api/app/backtest/cpcv.py:79]

Magic number `5` como mínimo bars por fold. Razonable pero no documentado. Considerar usar `purged_size + embargo_size + 10` para ligarlo a los parámetros.

### M5. `walk_forward.py:86-87` calcula meses como `30 días`
[/Users/samuelperez/trading/apps/api/app/backtest/walk_forward.py:86-87]

```python
is_delta = timedelta(days=is_months * 30)
oos_delta = timedelta(days=oos_months * 30)
```

30 días por mes es una aproximación; para `is_months=12` da 360 días, no 365.25. Sobre un rango de 4 años acumula ~21 días de error. No afecta correctitud matemática.

### M6. Dead-code: `probability_of_overfit` en `metrics.py` no se llama desde `cpcv.py`
[/Users/samuelperez/trading/apps/api/app/backtest/cpcv.py:36, 128]

Importado pero `pbo: float | None = None` se hardcodea. La función queda accesible vía API pública del módulo, lo cual está bien (preparada para CPCV real). Documentado.

### M7. `StrategyMetrics.overfit_warning` usa `max_drawdown > 0.5` como condición adicional
[/Users/samuelperez/trading/apps/api/app/backtest/metrics.py:352]

`overfit = (dsr < 0.5) or (max_dd > 0.5)`. Mezclar dos criterios distintos en el mismo flag puede confundir al agente cuando lee `overfit_warning=True` — un drawdown del 50% no es "overfit", es "risk-blowup". Separar en dos flags (`overfit_warning` y `risk_warning`) sería más claro.

---

## ✅ Lo que está bien

- **Refactor estructural limpio**: imports OK, `routes_backtests` + `routes_strategies` están en `main.py:12-13, 80-81`. Consumers externos (`agent/tools/run_backtest.py`, `agent/tools/cpcv.py`, `agent/tools/walk_forward.py`, `journal/routes.py`, `agent/validators.py`, `setups/repo.py`) importan desde la API pública del paquete (`from app.backtest import ...` o submódulos concretos). Ningún archivo huérfano.

- **PSR Mertens variance correcta** (metrics.py:202-223). Verificada numéricamente:
  ```
  code: 1 + 0.5·SR² - skew·SR + kurt_excess/4·SR²
  paper (Bailey 2012 eq 8 con γ4 = kurt_excess+3):
       1 - skew·SR + (γ4-1)/4·SR²
       = 1 - skew·SR + (kurt_excess+2)/4·SR²
       = 1 + 0.5·SR² - skew·SR + kurt_excess/4·SR² ✓
  ```

- **Sharpe per-observation** (NO anualizado) usado dentro de PSR/DSR via `sharpe_per_trade = trade_rets.mean() / trade_rets.std(ddof=1)` (metrics.py:308) — convención correcta de Bailey & López de Prado. La anualización solo aplica al campo `sharpe` mostrado al usuario.

- **`ddof=1`** consistente en todas las std/var (metrics.py:88, 102, 156, 166; cpcv.py:107, 109, 110) — sample-stats correcto.

- **No-lookahead** verificado por `test_no_lookahead.py` con la convención open-of-next-bar para entries/exits y stops intra-bar. Comentarios en runner.py:113-126 son explícitos sobre el bug histórico (close-to-close → look-ahead). Test pasa byte-identidad para trades cerrados antes del cutoff.

- **Compounding real** (mark-to-market sobre equity vigente, no aritmético sobre initial) verificado por `test_simulate_compounds_real_buy_and_hold` y comentarios runner.py:82-87, 162-168, 190-197. Bug B1 de auditoría previa cerrado.

- **PBO correcto** dado el formato (ranks): rank 1 = mejor, OOS rank > median → overfit. Tests `test_pbo_zero_when_is_winner_always_top_oos` y `test_pbo_one_when_is_winner_always_below_median` cubren los dos extremos.

- **Inputs sanitizados**: todas las SQL queries usan `text()` + bind params (`runner.py:312, 348`; `routes_backtests.py:76, 108`; `factor_stats_repo.py:191-207`). `symbol.upper()` se normaliza en `runner.py:252, 330` y `routes_backtests.py:91`. `BacktestSpec` (Pydantic) valida `fees_bps >= 0`, `slippage_atr >= 0`, `initial_equity > 0`, `timeframe` ∈ Literal. Sin SQL injection viable.

- **Strategy registry**: decorador idempotente con guard contra duplicates (`strategies/__init__.py:77-78`). Frozen dataclass para `StrategyDef`. Imports en `__init__.py` + `routes_strategies.py` aseguran que el registry se popula al levantar el módulo.

- **Warm-up purge en walk-forward** (walk_forward.py:99-131) — patrón correcto para evitar que indicadores en NaN durante los primeros ~200 bars del OOS subestimen la edge. Se renormaliza la equity correctamente.

- **Stitching del agregado OOS** (walk_forward.py:170-181) — concatena las equity curves por fold rescalándolas multiplicativamente. Es la convención correcta para que DSR/PSR sobre el agregado sea coherente (no media de DSRs por fold, que sería sin-sentido para stats no-lineales).

- **`stop_distance` snapshot a signal-time** (runner.py:90-101, 226-228) — slippage no usa volatilidad futura. Bug histórico cerrado.

---

## Notas adicionales (fórmulas verificadas)

### PSR (Probabilistic Sharpe Ratio) — Bailey & López de Prado 2012 eq (8)
**Estado**: ✅ Correcta.

```
PSR(SR*) = Φ[ (SR - SR*) · √(N-1) / √(1 - γ3·SR + (γ4-1)/4·SR²) ]
```
donde γ3 = skew, γ4 = full kurtosis (no excess).

Código (con `kurt_excess = γ4 - 3`):
```
denom = 1 + 0.5·SR² - skew·SR + kurt_excess/4·SR²
sr_std = √(denom / (N-1))
PSR = Φ(z = (SR - SR*) / sr_std)
```

Equivalencia algebraica (γ4 = kurt_excess + 3):
```
(γ4-1)/4·SR² = (kurt_excess+2)/4·SR² = 0.5·SR² + kurt_excess/4·SR² ✓
```

### DSR (Deflated Sharpe Ratio) — Bailey & López de Prado 2014 §3
**Estado**: 🔴 Aproximación divergente del paper (C1).

Paper:
```
SR* = (1-γ)·Φ⁻¹(1 - 1/N) + γ·Φ⁻¹(1 - 1/(N·e))   ← closed-form
DSR = PSR(SR > SR*)
```

Código (EVT/Gumbel):
```
SR* = √(2·ln N) - (1-γ)/√(2·ln N)
```
EVT es asintóticamente equivalente pero +0.30..0.37 sobre el closed-form para N ∈ [2, 1000]. Conservador. Ver C1.

### PBO (Probability of Backtest Overfitting) — Bailey/Borwein/López de Prado 2016
**Estado**: ✅ Correcto como "median rank rule" (versión simplificada). La versión completa con logits no está implementada porque `cpcv.py` no genera N paths independientes (queda pendiente como documenta su docstring).

```
median_rank = (n+1)/2
PBO = fracción de folds donde el IS-best ranquea > median en OOS
```

### Sharpe per-observation
**Estado**: ✅ Correcto.

```python
sharpe_per_trade = trade_rets.mean() / trade_rets.std(ddof=1)  # NO ann
```
Bailey & López de Prado AFML §13.2 trabajan en escala per-observation. La anualización (`* √(bars_per_year)`) sólo se aplica al campo `sharpe` mostrado al usuario (metrics.py:88-90), nunca dentro de PSR/DSR. ✓

### Annualization factor por timeframe
**Estado**: ✅ Correcto para 24/7 crypto.

```python
_BARS_PER_YEAR = {"1m": 60·24·365, ..., "1d": 365}
```
Cripto sin mercado cerrado → 365 días, no 252. Coherente.

### Mertens variance derivation
La fórmula de Mertens (2002) que cita Bailey supone retornos independientes con momentos finitos. Para retornos por bar durante una posición abierta (mark-to-market autocorrelacionado), `N=bars` infla la confianza ~2-3× — por eso `compute_metrics` usa `n_eff = trade_rets.size` (metrics.py:313). Comentario en lines 301-305 es explícito. Correcto.

---

## Resumen para el caller

Refactor estructural OK (routers wired en main.py:80-81, sin huérfanos, imports limpios). **Críticos**: (1) DSR usa Gumbel/EVT en lugar de la closed-form de Bailey — más conservador pero divergente del paper (~+0.35 en SR\* para N=100); (2) `backtest_runs` y `strategy_metrics` no tienen `user_id` → DSR cross-tenant leak y privacy leak en `GET /backtests` cuando F4 abra a multi-user; (3) race condition en lectura/incremento de `n_runs`. **Importantes**: anualización hardcodeada a √252 en cpcv.py:109-110 mis-escala Sharpes intradía; trades open-during-warm-up leak unrealized PnL al OOS; CPU-bound corriendo en el event loop bloqueará F4 24/7. **El más serio**: C2 (`user_id` ausente) si F4 va multi-tenant; si no, C1 (DSR divergente del paper) por correctitud científica. PSR-Mertens, no-lookahead, compounding y PBO verificados ✓.
