# Auditoría — Calidad cuantitativa del Mastery Trader

**Fecha**: 2026-05
**Alcance**: indicadores, agente, validators, backtest, métricas, bias detection
**Método**: 3 auditores especializados en paralelo (Claude general-purpose subagents)

---

## TL;DR — veredicto ejecutivo

- **Núcleo numérico bien construido**: RSI Wilder, ATR Wilder, MACD 12/26/9, ADX textbook, no look-ahead probado por construcción, citation contract, R:R floor, prompt caching disciplinado, PSR/DSR con varianza Mertens, anualizador cripto 24/7.
- **3 errores que invalidan métricas headline hoy**: compounding falso, n=bars en PSR/DSR, PBO trivial.
- **Cripto-específicos ausentes**: funding rate, open interest, correlación BTC↔ALT.
- **Bias detection existe pero es opt-in** (no gate).
- **Roadmap propuesto**: 4 sprints en orden A→B→C→D para evolucionar de "indicator panel decente" a "copilot top-tier defendible".

> ⚠️ **Nota crítica añadida tras review**: el roadmap NO incluye más indicadores técnicos por encima de los actuales — hay riesgo de saturar al LLM con datos redundantes. Sólo se añaden ejes ORTOGONALES (funding/OI/correlación) y reglas de gobernanza.

---

## P0 — Bugs que invalidan resultados ahora

| # | Issue | Impacto | Archivo |
|---|---|---|---|
| 1 | **Compounding falso**: cada trade dimensiona sobre `initial_equity`, no `equity` vigente | CAGR/Calmar/MAR mienten — equity es aditiva, no multiplicativa | `backtest/runner.py:155,181` |
| 2 | **PSR/DSR con `n=bars`** ignorando autocorrelación serial | Confianza estadística inflada 2-3× | `backtest/metrics.py:277-279` |
| 3 | **PBO ≈ 0.5 trivial — no es CPCV real**, el código mismo lo admite | El agente cita un PBO inválido | `backtest/cpcv.py:60-122` |
| 4 | **`best_dsr` se agrega con `GREATEST`** sobre DSRs separados sin re-deflar | Anula la corrección DSR; max-trial bias intacto | `backtest/runner.py:267,317` |
| 5 | **Funding rate ignorado** en perpetuos USDT-M | Holding multi-día con bias long sobreestima edge 0.3-0.5%/mes | `backtest/runner.py` (no existe) |
| 6 | **RSI/ADX div-by-0**: pump puro → RSI=null cuando debería ser 100 | Datos rotos al LLM en mercados extremos | `indicators/core.py:67`, `trend.py:46-54` |
| 7 | **Indicators colisión**: pedir `bbands(20)` y `bbands(50)` → la segunda pisa silenciosamente la primera | Datos mezclados | `agent/tools/indicators.py:19-24` |
| 8 | **Position sizing ausente del schema** `TradeIdea`. `trader_profile.json` declara 1% riesgo + 5x leverage pero no se enforce | El copilot no puede sizear → user opera "a ojo" | `agent/models.py:68-106` |
| 9 | **Bias detection opt-in**, no gate. "*Consider* calling" en el prompt; `severity=high` no fuerza `no_trade` | El sesgo se inyecta en risk_notes pero no impide ejecución | `system_prompt.py:140`, `validators.py` |

---

## P1 — Degradan calidad pero no falsean

10. **Confluence engine demasiado simple** — sólo EMA stack. Sin momentum, volumen relativo, distancia-ATR, regime gating. Cualquier dashboard básico de TradingView ya hace esto.
11. **Sin tools cripto-específicas**: `get_funding_rate`, `get_open_interest`, `get_btc_correlation`. **Esto es lo que más diferencia un copilot pro.**
12. **R-multiple fallback nonsensical**: cuando no hay stop, `r_multiple = ret * 100` (`backtest/runner.py:188`) — contamina expectancy.
13. **TPs no validados monotónicos**: long con `[80k, 78k, 82k]` pasa (`validators.py:179` solo mira `targets[0]`).
14. **Stale-data warning loggea pero no escala a `confidence='low'`** (`indicators.py:121-122`).
15. **Bias detector v1 frágil**: ventana revenge FIJA 15min (un swing 4h jamás dispara), FOMO depende de tags del usuario, oversize sin normalizar por R-risk.
16. **Walk-forward sin purge** de samples IS cuyo trade cruza al OOS (`walk_forward.py:65-71`).
17. **Pivot lag invisible**: con `strength=3` en 15m, "último swing" es 45min viejo y no se comunica al LLM (`structure.py:56`).
18. **Single-link clustering en S/R**: puntos a 100, 100.2, 100.4 con tolerancia 0.25 se agrupan TODOS aunque extremos disten 0.4 (`structure.py:71-76`).
19. **`regime.label` puede contradecir `confluences`** sin error (validator trivial faltante).
20. **System prompt pide ≥2/3 confluences** alineadas pero validator sólo exige 1.

---

## P2 — Refinements (no urgentes)

21. **Métricas premium**: Pain Index, CDaR α=0.05, Rank IC, hit-rate por régimen, MinTRL (Bailey-LdP 2012), bootstrap CI del Sharpe.
22. **VWAP funding-anchored** (8h) + anchored a evento (último ATH/halving/swing).
23. **Volume profile (POC/HVN/LVN)** — UN tool de volumen bien hecho.
24. **Maker/taker dual fee + parcial fills** en backtest engine.
25. **Hash determinista de runs**: `sha256(strategy_id|params|symbol|tf|range|fees|seed|version)` — permite cache y citas estables.
26. **Seed propagation real**: `np.random.default_rng(seed)` cuando estrategias futuras usen randomness.

---

## ⚠️ Nota crítica: NO añadir indicadores técnicos por encima de los actuales

Tras review crítico, **se descartan** del roadmap original:
- ~~OBV, CMF, volume-delta~~ — los tres miden volumen, redundantes.
- ~~Múltiples osciladores nuevos~~ — RSI + MACD + ADX ya cubren momentum.
- ~~Divergencias automáticas~~ — el LLM las puede inferir de RSI + price.

**Razón**: cada indicador añadido va al contexto del LLM y compite por atención. Más data NO es más edge si los datos están correlados:

- **Lost-in-the-middle**: atención no uniforme; el centro del prompt se "pierde".
- **Confirmation bias amplificado**: con 20 tools, el LLM siempre encuentra 3 que confirman tesis preexistente.
- **Saturación**: 5 osciladores momentum ≈ 1 oscilador momentum. Información ortogonal (funding, OI, correlación) sí descubre, no satura.

**Lo que SÍ entra**: ejes nuevos que el bot hoy ignora (funding, OI, correlación, regime, volume profile UNO solo). Y reglas de gobernanza:
- Cap **6 tool calls por análisis** en system prompt.
- Regla **anti-confirmation-bias**: "llama tools para falsificar tu tesis, no para confirmarla".
- Justificación obligatoria de cada tool usada.

---

## Roadmap revisado

### Sprint A — "Fix the math" (~5-7 días)

**Objetivo**: que cualquier auditoría externa NO pueda destrozar las métricas que el copilot cita.

- [P0 #1] Compounding real en `_simulate`. Tests: equity de buy-and-hold == `(close[-1]/close[0]) × initial_equity`.
- [P0 #2] PSR/DSR con `n_eff` (Newey-West con lag = `avg_bars_held`) o `n_trades` para SR per-trade.
- [P0 #3] Renombrar `cpcv.py` a `block_bootstrap_sharpe.py` Y/O implementar CPCV real (re-ejecutar signals por fold).
- [P0 #4] Re-deflar `best_dsr` con `n_trials = strategy_metrics.n_runs` cada update.
- [P0 #5] Modelar funding rate cada 8h en `_simulate`. Storage `funding_rates` table.
- [P0 #6] RSI/ADX div-by-0.
- [P0 #7] `_GROUPED_OUTPUTS` parametrizado por longitud.

### Sprint B — "Make it actionable" (~5 días)

**Objetivo**: el copilot puede decir "no operes hoy" Y dimensiona el trade.

- [P0 #8] `position_size_pct: float | None` en `TradeIdea` + regla en prompt para calcularlo desde `risk_per_trade_pct`.
- [P0 #9] Bias gate hard: parsear output `detect_bias_patterns`, `severity=high` → `direction='no_trade'` o `confidence='low'`. Cambiar prompt de "consider" a "MUST".
- [P1 #13] TPs monotónicos.
- [P1 #19] regime↔confluences coherentes.
- [P1 #20] ≥2 confluences direccionales.
- SL distance floor (`<5·ATR`).
- **Reglas de gobernanza** en system prompt: cap 6 tool calls + anti-confirmation-bias + justificación obligatoria.

### Sprint C — "Crypto edge" (~7 días)

**Objetivo**: el copilot ve lo que un trader pro mira y los dashboards retail no.

- [P1 #11] Tool `get_funding_rate` (current + 7d cumulative).
- [P1 #11] Tool `get_open_interest` (delta + tendencia).
- [P1 #11] Tool `get_btc_correlation(symbol, lookback)` para ponderar bias en altcoins.
- [P1 #10] Confluence v2 multifactor: EMA-stack (0.3) + ADX/regime (0.2) + RSI extremo (0.15) + volumen relativo (0.15) + distancia-EMA en ATR (0.2).
- [P1 #12] R-multiple fix cuando no hay stop.
- [P2 #23] Volume profile UN solo tool (POC/HVN/LVN).

### Sprint D — "Polish + premium metrics" (~5 días)

**Objetivo**: refinements que separan "pro" de "élite".

- [P1 #15] Stale-data → confidence='low' automatic.
- [P1 #17] Pivot lag explícito en provenance.
- [P1 #18] Complete-linkage clustering en S/R.
- [P1 #16] Bias detector v2 con holding time real (depende de F4 paper).
- [P1 #16] Walk-forward purge.
- [P2 #21] Pain Index, CDaR, Rank IC, MinTRL, bootstrap CI del Sharpe.

---

## Decisión inicial recomendada

**Sprint A items 1-4 + Sprint B items 8 (sizing) + 9 (bias gate)**: combo de mayor impacto inmediato. Cierra los gaps de credibilidad (matemática) y los gaps de utilidad (sizing + bias gate). El user obtiene un copilot que (a) cita métricas defendibles, (b) le dice cuánto arriesgar, (c) le frena cuando emocional.

Sprint C y D vienen después, una vez el cimiento es sólido.

---

## Referencias

- Bailey, López de Prado (2012, 2014) — Sharpe Probabilístico, DSR, MinTRL.
- López de Prado (2018), *Advances in Financial Machine Learning* — CPCV, PBO.
- Welles Wilder (1978), *New Concepts in Technical Trading Systems* — RSI, ATR, ADX SMMA.
- Mertens, E. (2002) — varianza del Sharpe estimado.
- Bollinger, J. (2002), *Bollinger on Bollinger Bands* — convención ddof=0.
