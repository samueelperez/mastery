# Trading Copilot — Plan Maestro

<purpose>
Este documento da a Claude Code el contexto estratégico del proyecto. Es complementario a `CLAUDE.md` (reglas de código globales) y a los specs por módulo. Léelo antes de empezar cualquier trabajo nuevo en el repo. Si el contexto importa para decidir cómo implementar algo, está aquí.
</purpose>

<resumen_ejecutivo>
Trading Copilot es un sistema autónomo personal (NO SaaS, single-user) que ejecuta trading de futuros perpetuos cripto en Hyperliquid. La arquitectura usa **7 cerebros especializados** orquestados por un LLM (Claude Sonnet 4.6), con gates determinísticos de riesgo, validación contra suscripción manual de TradingDifferent durante el periodo de bring-up, y autonomía total como objetivo a 90 días. El stack está en producción parcial: 12 módulos existentes documentados; estamos añadiendo los 7 cerebros uno por uno empezando por el Cerebro 1 (Liquidation Heatmap Engine).
</resumen_ejecutivo>

## 1. Contexto del operador

- **Perfil**: Full-stack AI developer con experiencia previa en trading manual de cripto perps.
- **Ubicación**: Madrid, España (jurisdicción UE — MiCA aplicable).
- **Capital de partida**: €1.000 explícitamente etiquetados como "capital de aprendizaje + retorno". Costes de infra (€60-100/mes) son separados, no salen de este capital.
- **Validación previa**: el operador ha usado durante varios meses la herramienta de heatmap de liquidación de TradingDifferent (suscripción anual activa) y la considera empíricamente la mejor señal disponible para su estilo. Esto NO es opinión: es ground truth experimental que sustenta toda la arquitectura del Cerebro 1.
- **Modelo de delegación**: el operador implementa con Claude Code; revisa PRs por día (uno por día según el plan); aprueba operaciones desde Telegram en el móvil pero **no puede validar manualmente la corrección técnica de cada setup** — por eso el sistema necesita citation contract y validators rigurosos.
- **Objetivo declarado**: construir "algo que no existe aún". No es una réplica de un bot público. La diferenciación está en la fusión multi-cerebro + autonomía progresiva calibrada por ground truth.

## 2. Contexto regulatorio (Feb 2026 update)

Lectura obligada antes de tocar venue/exchange.

- **MiCA pleno desde 30/12/2024**; transición ESP hasta 30/12/2025 (ya cerrada). Aplicación retail completa **a partir del 1/7/2026** en España.
- **ESMA Q&A Feb 2026**: clasificó perps cripto como **CFDs** a efectos retail UE. Implicación: **leverage máximo retail = 2×** en venues regulados UE.
- **Binance USDM Futures**: bloqueado para retail UE desde 2024. Solo accesible vía Binance.com con KYC no-UE o testnet. **Usamos solo testnet** para paper trading.
- **Hyperliquid**: DEX no establecido en UE, no solicita activamente clientes UE. Acceso del operador cae bajo **Art. 61.2 MiCA (reverse solicitation)** — operador inicia la relación; no hay marketing dirigido. Riesgo legal: bajo, pero no nulo. Documentación de la decisión queda en este plan maestro.
- **Implicación arquitectónica**:
  - Venue live = Hyperliquid (sin gate de leverage 2×; usamos nuestro propio gate interno de 3× máximo).
  - Venue dev/paper = Binance testnet (sin riesgo regulatorio porque es testnet sin valor real).
  - Si MiCA endurece reverse solicitation post-julio 2026, el sistema migra a otro venue cripto no-UE o se pone en modo paper-only. Decisión a tomar en M3.

No se contempla operativa fiat sobre el sistema. Capital se mueve en USDC sobre Arbitrum/Solana hacia Hyperliquid manualmente. Cero exposición a stablecoins UE bajo MiCA art. 23 (Tether/USDT).

## 3. Arquitectura: los 7 cerebros

El orquestador (LLM Sonnet 4.6) NO es el decisor solitario de entradas. Esa decisión está fundamentada en investigación 2025-2026 (StockBench, BlindTrade, papers sobre AMA): los LLMs sistemáticamente NO baten al mercado en live trading cuando se les deja decidir sin gates determinísticos.

El LLM es **orquestador-integrador**: pide datos a los 7 cerebros, fusiona, propone setups, los pasa por gates determinísticos, y si pasan los gates, los envía a aprobación humana (M1) o ejecuta directo (M3).

| # | Cerebro | Estado | Tool exposed |
|---|---------|--------|--------------|
| 1 | Liquidation Heatmap Engine | M1 (este sprint) | `get_liquidation_heatmap` |
| 2 | Mean-Reversion Engine | M2 | `get_mean_reversion_signal` |
| 3 | Trend & Momentum Engine | M2 | `get_trend_state` |
| 4 | Volatility Regime Engine | M3 | `get_volatility_regime` |
| 5 | On-Chain Flow Engine | M3 | `get_onchain_flow` |
| 6 | News & Sentiment Engine | M4 | `get_news_sentiment` |
| 7 | Funding & Basis Engine | M4 | `get_funding_basis` |

Cada cerebro:
- Tiene su propio módulo bajo `apps/api/app/<cerebro>/`.
- Expone exactamente UNA tool a la agent.
- Devuelve `ToolResult[T]` con `Provenance`.
- Tiene su propio citation contract en `agent/validators.py`.
- Tiene su propia tabla de métricas en Prometheus.

Razón para 7 y no más: los papers de fusión multi-señal muestran rendimientos marginales decrecientes después de 6-8 fuentes ortogonales. Más de 7 = overfitting al ruido del entrenamiento.

Razón para 7 y no menos: cada cerebro cubre una dimensión ortogonal del problema (microestructura, mean-reversion, trend, vol, on-chain, news, term structure). Quitar uno deja un blind spot.

## 4. Rol del LLM y stack de modelos

| Función | Modelo | Razón |
|---------|--------|-------|
| Scout (binary decisiones rápidas) | `anthropic/claude-haiku-4.5` (OpenRouter id) | Latencia <1s, coste 1/10 de Sonnet — migrado en PR-07 (ADR-003) |
| Chat operador | `anthropic/claude-sonnet-4.6` | Razonamiento multi-paso, citation rigor |
| Reviewer agent (post-entry pre-cierre) | `anthropic/claude-sonnet-4.6` (`thinking="low"`) | Pre-cierre rápido — ver ADR-002 |
| Post-mortem agent (post-cierre) | `anthropic/claude-sonnet-4.6` (`thinking="medium"`) | Análisis terminal — ver ADR-002 |
| Audit on-demand manual | `anthropic/claude-opus-4-7` | Análisis profundo, plumbed pero sin toggle UI hasta F2 |

Reviewer y post-mortem son **dos agentes independientes**, no un Supervisor fusionado: system prompts divergentes, output types distintos (`TradeReview` vs `PostMortem`), thinking levels diferentes. Versión anterior del PLAN proponía fusión; revisada en [[docs/adr/002-reviewer-and-postmortem-stay-separate.md]] tras observar el coste de cache hit que la fusión impondría.

Tools en el agente principal: **23 hoy**, target 8 a alcanzar en el boundary M1/M2 con datos de uso reales. Ver [[docs/adr/001-tool-inventory-deferred-to-m2.md]]. La lista alfabética actual (commit-time) se enumera en `apps/api/app/agent/agent.py::build_agent()`. Razón para reducir: cada tool extra añade ~150 tokens al system prompt (caché) + degrada la atención del LLM al elegir entre opciones. El target final puede ser 8 o algo distinto — lo decide la telemetría per-tool del M1/M2 eval, no la intuición.

## 5. Stack técnico (resumen)

Detalle completo en `CLAUDE.md::tech_stack_constraints`. Resumen:

- **Python 3.12+** | FastAPI | SQLAlchemy 2 async + asyncpg | PostgreSQL 16 + TimescaleDB + pgvector
- **Polars** para DataFrames (no pandas en código nuevo)
- **httpx** async para HTTP (no requests, no aiohttp)
- **ccxt.pro** para Binance/Bybit; **cliente custom** para Hyperliquid (CCXT no soporta clearinghouseState)
- **Valkey** (Redis wire-compatible) para cache/pubsub
- **pydantic-ai** para agentes
- **OpenRouter** en dev/paper, **Anthropic direct** en live (DR fallback OpenRouter)
- **voyage-3-large** para embeddings (diferido hasta ≥200 entradas en journal)
- **structlog** JSON renderer + **prometheus_client**
- **pytest** + **pytest-asyncio** mode=auto + **hypothesis** para property-based

Despliegue: Railway (1 servicio API + 1 worker + Postgres + Valkey). Coste mensual M1-M2: €60-90.

## 6. Risk management y autonomía progresiva

Los gates son **determinísticos**, **leídos desde Settings** (jamás hardcoded), y se aplican ANTES de que el LLM tenga oportunidad de "razonar" sobre saltárselos.

### Gates duros (no negociables)

| Gate | Valor | Comportamiento al hit |
|------|-------|----------------------|
| Risk per trade | 0.75% equity | Tamaño calculado, no rechazo |
| Max leverage por posición | 3× | Tamaño reducido al ratio que cabe |
| Max gross leverage | 1.5× | Rechazo del nuevo setup |
| Daily loss | -3% equity | Freeze 24h, no nuevos setups |
| Max drawdown | -10% desde HWM | Manual unlock requerido |
| Trades por día | 3 | Rechazo del 4º setup |
| Cooldown 2 SL consecutivos | 4 horas | No nuevos setups en ese símbolo |
| Cooldown 3 SL consecutivos | 24 horas | No nuevos setups en ese símbolo |
| News blackout | ±15min eventos high-impact | Rechazo de aperturas; cierres permitidos |
| Min R:R ratio | 1.5 | Rechazo del setup |
| Min factor LCB (lower confidence bound) | 0.42 | Rechazo del setup |
| Min expectancy LCB (R-multiples) | 0.25 | Rechazo del setup |
| Approval timeout (M1-M2) | 90 segundos | Rechazo automático del setup |

### Autonomía progresiva (3 fases)

**Fase 1 (M1, días 0-30)** — Aprobación manual obligatoria.
- Cada setup pasa por Telegram con inline keyboard.
- Operador aprueba/rechaza desde móvil en ≤90s.
- Sin respuesta = rechazo automático.
- **+** Validación ground truth contra TradingDifferent (3 botones extra: agree/close/disagree).

**Fase 2 (M2, días 30-60)** — Auto-approve por confidence.
- Setups con `confidence='high'` Y `sources_agreement>=0.85` Y todos los gates verdes → ejecución directa, notificación a posteriori.
- Setups con `confidence='medium'` → aprobación manual.
- Setups con `confidence='low'` → rechazo automático en pre-gate.
- Ground truth collection DESACTIVADO (toggle `ground_truth_collection_enabled=False`).

**Fase 3 (M3+, días 60+)** — Autonomía total.
- Condiciones de activación (TODAS):
  - DSR (Deflated Sharpe Ratio) sostenido > 0.5 sobre 60 días rolling.
  - Max drawdown < 15% en el periodo.
  - 90 días de paper trading completados sin race conditions.
  - 0 violaciones de invariantes en logs.
- Aprobación manual sigue disponible como override, pero no es la ruta default.

## 7. Roadmap 30 / 60 / 90 / 180 días

### Días 0-30 (M1) — Cerebro 1 operativo + paper trading
- Implementar Cerebro 1 según los specs en `docs/cerebro1/` (rename de la antigua `docs/specs/liquidation/`).
- Activar paper trading sobre Binance testnet con BTC/ETH/SOL.
- 4 semanas de validación ground truth contra TradingDifferent.
- Empezar a poblar `liquidation_agreement_log`.

### Días 30-60 (M2) — Cerebros 2-3 + activación pesos adaptativos
- Implementar Mean-Reversion Engine (Cerebro 2).
- Implementar Trend & Momentum Engine (Cerebro 3).
- Ejecutar `compute_provider_weights` semanal sobre el agreement log.
- **Decisión binaria sobre Coinglass**: si agreement vs TD < 80% sobre 4 semanas, activar provider Coinglass ($29/mes). Si ≥ 80%, NO activar.
- Migrar de Binance testnet a Hyperliquid live con €100 de los €1.000 (test pequeño).

### Días 60-90 (M3) — Cerebros 4-5 + autonomía progresiva
- Implementar Volatility Regime Engine (Cerebro 4).
- Implementar On-Chain Flow Engine (Cerebro 5).
- Habilitar auto-approve por confidence (Fase 2 de autonomía).
- Si métricas verdes, activar autonomía total (Fase 3).

### Días 90-180 (M4) — Cerebros 6-7 + escala
- Implementar News & Sentiment Engine (Cerebro 6).
- Implementar Funding & Basis Engine (Cerebro 7).
- Si capital ha crecido significativamente, ampliar watch list a 6 símbolos.
- Considerar arbitraje funding entre Hyperliquid y otro DEX (decisión a tomar con datos en mano).

## 8. Decisiones rechazadas (no las re-litigues)

Estas decisiones ya fueron tomadas tras análisis. Si Claude Code se ve tentado a sugerir alguna de estas, RECHAZAR sin entrar en debate. Si hay evidencia nueva que cambie el balance, plantear como issue separado, no como deviation in-flight.

| Idea descartada | Razón |
|----------------|-------|
| Coinglass desde día 1 | Coste $29/mes + complejidad rate-limit. Diferido a M2 con decisión binaria empírica. |
| `py-liquidation-map` como dependencia | 119 stars, sin actividad desde 2023. Usamos su lógica conceptual (~200 LoC) en código propio. |
| Scraping de TradingDifferent | Cloudflare protection + ToS lo prohíbe + el operador ya tiene acceso manual. Lo usamos vía Telegram ground truth, no programático. |
| LLM como decisor solitario de entradas | Papers 2025-2026 muestran que LLMs no baten al mercado en live. Rol = orquestador con gates determinísticos. |
| 22 tools en main agent | Degrada atención del LLM. Target 8; purga real diferida al boundary M1/M2 con telemetría real — ver [[docs/adr/001-tool-inventory-deferred-to-m2.md]]. |
| Reviewer + post_mortem fusionados en Supervisor único | Revisada en M1-polish: divergent prompts + thinking levels + output types. Se mantienen como 2 agentes — ver [[docs/adr/002-reviewer-and-postmortem-stay-separate.md]]. |
| Scout reusa el main agent (Sonnet) | Migrado a Haiku 4.5 dedicado en PR-07 — ~10× ahorro coste. Ver [[docs/adr/003-scout-migrated-to-haiku-4-5.md]]. |
| Binance USDM Futures retail UE | Bloqueado regulatoriamente. Solo testnet. |
| Pandas en código nuevo | Polars es 5-30× más rápido en pipelines de heatmap. Pandas solo donde el código legacy lo usa. |
| Asyncio.create_task sin tracking | Tasks huérfanas son la fuente #1 de race conditions. TODO task está named + tracked en lifespan. |
| float para valores monetarios | Decimal con prec=28 y ROUND_HALF_EVEN. float solo para bps/% scale. |
| Sin user_id en queries (somos single-user) | Defense in depth. Cuesta 0; protege contra bugs futuros y eventual multi-user. |

## 9. Invariantes de producto (no de código)

Las invariantes técnicas están en `CLAUDE.md::critical_invariants`. Estas son invariantes de **comportamiento del producto** que el código debe respetar pero no son chequeables con un linter:

1. **El sistema nunca opera sin aprobación humana hasta cumplir las 3 condiciones de Fase 3.** Aunque el código compile y los tests pasen, si no se cumplen DSR>0.5 + DD<15% + 90 días limpios, el toggle de autonomía total queda OFF.
2. **El operador siempre puede pulsar "freeze" desde Telegram.** Esto debe estar disponible incluso en autonomía total. Comando: `/freeze` cierra todo y bloquea 24h.
3. **Los gates determinísticos preceden al razonamiento del LLM.** Si un setup falla un gate, el LLM no debería siquiera ver ese setup como opción. El gate corre primero en `setups/service.py::propose_setup`.
4. **Ningún cerebro toma decisiones operativas.** Los 7 cerebros producen datos. Las decisiones se toman en `setups/` (sizing, leverage) y `paper_trading/` o `live_trading/` (ejecución).
5. **El sistema asume que TODOS los providers externos pueden caer.** Fail-closed: si un provider crítico no responde, el setup se rechaza con `provider_failed`. Nunca rellenamos con datos del último snapshot.
6. **La citation contract es ley.** Cualquier setup que cite una zona/indicador/factor sin que la tool real haya devuelto ese dato es rechazado por el validator. Sin excepciones.
7. **Los riesgos numéricos vienen de `Settings`, no de constantes hardcoded en módulos.** Esto permite ajustar en producción sin redeploy y testear con valores extremos.
8. **Cada operación deja huella en `journal_trades` con `factor_snapshot` completo.** Sin journal completo, el post-mortem es ciego y la calibración futura es imposible.

## 10. Cómo usar este documento (instrucciones para Claude Code)

Cuando arranques una sesión nueva:

1. **Lee `CLAUDE.md`** primero — son las reglas de coding.
2. **Lee este `PLAN_MAESTRO.md`** si necesitas entender el por qué de una decisión arquitectónica.
3. **Lee el spec específico** del módulo en `docs/specs/<module>/` para implementar.
4. Si el spec contradice este plan maestro, **el spec gana** para detalles de implementación. Si la contradicción afecta a una invariante de producto (sección 9), **detente y pregunta al operador**.
5. Si encuentras una decisión arquitectónica que parece dudosa y NO está en la sección 8 ("Decisiones rechazadas"), siéntete libre de plantearla en el PR description. El operador la considerará. Las que SÍ están en la sección 8 no se re-debaten.

Este documento NO es código y NO se versiona como spec ejecutable. Es contexto. Se actualiza cada vez que se cierra un milestone (M1, M2, M3) o cuando una decisión estratégica cambia (con ADR adjunto en `docs/adr/`).

## Anexo A — Glosario rápido

- **DSR**: Deflated Sharpe Ratio (Bailey & López de Prado 2014). Sharpe ajustado por trials múltiples, evita overfit.
- **LCB**: Lower Confidence Bound. En factor stats usamos Beta(2, 2.5) prior para win-rate.
- **Magnet zone**: rango de precios donde se concentran liquidaciones de posiciones apalancadas. Actúa como imán de precio.
- **Citation contract**: convención que obliga al LLM a citar la fuente real (tool output) de cada claim numérico en un setup. Enforced por validator.
- **Ground truth (en este proyecto)**: verdict del operador sobre si TradingDifferent confirma una zona propuesta. NO se usa para runtime; SOLO para calibrar pesos adaptativos de providers.
- **Reverse solicitation (Art. 61.2 MiCA)**: cliente UE inicia relación con proveedor no-UE sin marketing dirigido. Excepción a la obligación de autorización MiCA.
- **Cerebro**: módulo especializado en una dimensión del análisis (microestructura, mean-reversion, trend, etc.). Expone exactamente 1 tool al agente.

## Anexo B — Métricas de éxito por milestone

**M1 (día 30)**:
- Cerebro 1 operativo 99% del tiempo.
- ≥50 setups propuestos por el sistema.
- ≥40 ground-truth verdicts recogidos (mínimo para empezar a calibrar).
- 0 violaciones de invariantes en logs.
- Paper trading sin race conditions.

**M2 (día 60)**:
- 3 cerebros operativos.
- Pesos adaptativos calculados al menos 1 vez.
- Decisión Coinglass tomada con datos.
- Primera operación live en Hyperliquid (€100 capital).
- Drawdown máximo en paper < 5%.

**M3 (día 90)**:
- 5 cerebros operativos.
- Fase 2 de autonomía habilitada (auto-approve por confidence).
- Si métricas verdes: Fase 3 (autonomía total) activada.
- DSR > 0.5 sostenido en últimos 60 días.

**M4 (día 180)**:
- 7 cerebros operativos.
- Autonomía total operando con seguridad.
- Capital crecido (sin objetivo numérico — depende de mercado).
- Watch list expandida si métricas lo justifican.

---

*Última actualización: 12 de mayo de 2026. Próxima revisión: cierre de M1 (~12 de junio de 2026).*
