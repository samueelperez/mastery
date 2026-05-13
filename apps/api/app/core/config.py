from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        default="postgresql+asyncpg://trading:trading@localhost:5432/trading",
        alias="DATABASE_URL",
    )

    @field_validator("database_url", mode="before")
    @classmethod
    def _ensure_asyncpg_driver(cls, v: object) -> object:
        """Promociona `postgresql://` → `postgresql+asyncpg://` automáticamente.

        Railway, Supabase, Heroku, Neon… todos exponen DATABASE_URL como
        `postgresql://` (o el viejo `postgres://`). El stack usa asyncpg como
        driver async, que SQLAlchemy resuelve con el dialect prefix
        `postgresql+asyncpg`. En lugar de pedirle al user que añada el
        prefix manualmente (frágil), lo hacemos aquí.
        """
        if not isinstance(v, str):
            return v
        s = v.strip()
        if s.startswith("postgres://"):
            return "postgresql+asyncpg://" + s[len("postgres://") :]
        if s.startswith("postgresql://"):
            return "postgresql+asyncpg://" + s[len("postgresql://") :]
        return s

    valkey_url: str = Field(
        default="redis://localhost:6379/0",
        alias="VALKEY_URL",
    )

    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    api_log_level: str = Field(default="INFO", alias="API_LOG_LEVEL")

    cors_origins: str = Field(
        default="http://localhost:3000,http://localhost:3001",
        alias="CORS_ORIGINS",
    )

    # ---- Main agent model config (audit fix 2026-05) -----------------------
    # Antes hardcoded en agent.py — promovido a Settings para poder tunear
    # max_tokens / thinking / retries sin re-deploy ni redeploy de imagen.
    agent_max_tokens: int = Field(default=24000, alias="AGENT_MAX_TOKENS")
    agent_thinking: str = Field(default="medium", alias="AGENT_THINKING")
    agent_retries: int = Field(default=2, alias="AGENT_RETRIES")

    # ---- DB pool config (audit fix 2026-05) -------------------------------
    db_pool_size: int = Field(default=10, alias="DB_POOL_SIZE")
    db_pool_max_overflow: int = Field(default=5, alias="DB_POOL_MAX_OVERFLOW")
    # `pool_recycle` evita conexiones stale en hosts que cierran idle (Neon,
    # Supabase free tier). 1800s = 30min. -1 = no recycle (SQLAlchemy default).
    db_pool_recycle_s: int = Field(default=1800, alias="DB_POOL_RECYCLE_S")

    # LLM provider — F1 chat agent. Pydantic AI's OpenRouterProvider reads this
    # directly from the env, but we surface it on Settings so /health can flag a
    # missing key cleanly and tests can override it.
    openrouter_api_key: str | None = Field(default=None, alias="OPENROUTER_API_KEY")

    # Embeddings — F2 journal retrieval (voyage-4-large @ 1024 dim).
    voyage_api_key: str | None = Field(default=None, alias="VOYAGE_API_KEY")

    # Multi-symbol watchlist — F-multi. CSV de pares USDT-M de Binance que la
    # ingesta live mantiene streamando + persistiendo. La sidebar del frontend
    # los expone en su orden. Cualquier símbolo aquí se backfilla en arranque.
    watch_symbols: str = Field(
        default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT",
        alias="WATCH_SYMBOLS",
    )

    # ---- Trade Reviews (F5 — post-entry auto-review) ---------------------
    # Cooldown mínimo entre reviews del MISMO setup. Bloquea spam cuando dos
    # triggers caen casi simultáneos (entry_hit + time_elapsed).
    review_cooldown_min_minutes: int = Field(default=30, alias="REVIEW_COOLDOWN_MIN_MINUTES")
    # Hard cap por setup. Coste runaway protection — 12 reviews × max_cost ≈
    # tope predecible. Tras alcanzarlo, claim_review_slot rebota.
    review_max_per_setup: int = Field(default=12, alias="REVIEW_MAX_REVIEWS_PER_SETUP")
    # Concurrencia global del review_agent. Si 5 setups hacen entry_hit en
    # la misma vela, 2 corren en paralelo y el resto encola.
    review_concurrency: int = Field(default=2, alias="REVIEW_CONCURRENCY")
    # Time-based scheduler: dispara `time_elapsed` cuando entry_hit_at +
    # offset_h ≤ now. Hardcoded CSV de offsets en horas.
    review_time_offsets_h: str = Field(default="4,24,72", alias="REVIEW_TIME_OFFSETS_HOURS")
    # Price-move trigger: umbral por defecto en % (override por TF si quieres
    # afinarlo ATR-relative en el código).
    review_price_move_pct: float = Field(default=2.0, alias="REVIEW_PRICE_MOVE_THRESHOLD_PCT")
    # Approaching-SL trigger: dispara cuando el precio cubrió este fracción
    # del camino entry → SL (1.0 = ya en SL).
    review_approaching_sl_pct: float = Field(default=0.75, alias="REVIEW_APPROACHING_SL_FRACTION")
    # Pricing (USD per 1M tokens) — usado para estimar cost_usd del agente.
    review_price_input_per_m_usd: float = Field(default=3.0, alias="REVIEW_PRICE_INPUT_PER_M_USD")
    review_price_output_per_m_usd: float = Field(
        default=15.0, alias="REVIEW_PRICE_OUTPUT_PER_M_USD"
    )
    review_price_cache_read_per_m_usd: float = Field(
        default=0.3, alias="REVIEW_PRICE_CACHE_READ_PER_M_USD"
    )
    # Hard timeout on agent.run() — sin esto, una OpenRouter colgada bloquea
    # un slot del semáforo indefinidamente. Audit fix 2026-05.
    review_timeout_s: float = Field(default=90.0, alias="REVIEW_TIMEOUT_S")

    # ---- Post-Mortem (F5.5 — análisis terminal + feedback loop) ----------
    # Master flag del subsistema. Default False: el dispatcher es no-op hasta
    # que se flippea (rollout gradual: data layer primero, agente después).
    post_mortem_enabled: bool = Field(default=False, alias="POST_MORTEM_ENABLED")
    # Concurrencia global. Bursts ocurren cuando régimen flipea y N setups
    # cierran a la vez — 2 corre en paralelo y el resto encola.
    post_mortem_concurrency: int = Field(default=2, alias="POST_MORTEM_CONCURRENCY")
    # Auto-inject del preamble <historic_stats> en cada user message del
    # /chat principal. Cero overhead de retry; el agente ve las stats
    # Bayesian en cada turno. Default False — flippear cuando haya datos
    # acumulados (1-2 semanas de cierres post post_mortem_enabled).
    historic_stats_preamble_enabled: bool = Field(
        default=False, alias="HISTORIC_STATS_PREAMBLE_ENABLED"
    )
    # Validator soft gate. Cuando el TradeIdea cita un factor con
    # win_rate_lcb<25% sin caveat explícito, raise ModelRetry. Default False:
    # se activa solo tras 1 semana de preamble probado.
    factor_stats_gate_enabled: bool = Field(default=False, alias="FACTOR_STATS_GATE_ENABLED")
    # % de cierres marcados is_holdout=TRUE (out-of-sample para anti-overfit
    # del feedback loop). Determinista por hash(trade_id || user_id).
    holdout_pct: int = Field(default=15, alias="HOLDOUT_PCT")

    # ---- F4 — Paper trading engine -----------------------------------------
    # Master flag. Default False — entry/exit en setups/runtime persiste los
    # transitions pero NO toca paper_positions. Activar tras smoke test.
    paper_trading_enabled: bool = Field(default=False, alias="PAPER_TRADING_ENABLED")
    # Equity inicial por user al hacer init_balance. Configurable para tests.
    paper_initial_equity_usd: float = Field(
        default=10_000.0, alias="PAPER_INITIAL_EQUITY_USD"
    )
    # Taker fee bps por defecto (Binance USDT-M = 4 bps = 0.04%). Aplicado
    # tanto en entry como en exit. Migrar a per-exchange en F4.1.
    paper_taker_fee_bps: float = Field(default=4.0, alias="PAPER_TAKER_FEE_BPS")
    # Spread por defecto cuando no hay L1 orderbook ingestor (F4.0). Override
    # cuando F4.1 cablee real spread depth.
    paper_default_spread_pct: float = Field(
        default=0.02, alias="PAPER_DEFAULT_SPREAD_PCT"
    )

    # ---- C.3 — Telegram bot ------------------------------------------------
    # When TELEGRAM_BOT_TOKEN is empty (default) the notifications module
    # silently degrades — endpoints return 503 / scout_dispatcher logs and
    # continues. Set the token AND publish a webhook URL pointing at this
    # API to enable delivery.
    telegram_bot_token: str | None = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    # Shared secret in the webhook URL so unsolicited POSTs from random
    # crawlers can't impersonate Telegram updates. Telegram sends every
    # update with this value as a header (X-Telegram-Bot-Api-Secret-Token).
    telegram_webhook_secret: str | None = Field(
        default=None, alias="TELEGRAM_WEBHOOK_SECRET"
    )
    # Base URL the bot links to from inline buttons (chart, setup detail).
    # Defaults to the dev frontend; production should override.
    telegram_app_base_url: str = Field(
        default="http://localhost:3001", alias="TELEGRAM_APP_BASE_URL"
    )
    # TTL for the one-time bind code that links a Telegram chat to a user.
    telegram_bind_code_ttl_seconds: int = Field(
        default=600, alias="TELEGRAM_BIND_CODE_TTL_SECONDS"
    )

    # Cerebro 1 (Day 6): when True, setup alerts include 3 ground-truth
    # buttons (✅ agree / ⚠️ close / ❌ disagree) for TradingDifferent manual
    # validation. Disable at the start of M2 once weights have been
    # calibrated (~4 weeks of data). Toggling it back on in M3+ is the
    # escape hatch if the system drifts.
    ground_truth_collection_enabled: bool = Field(
        default=True, alias="GROUND_TRUTH_COLLECTION_ENABLED"
    )

    # ---- B.2 — Slippage buffer (pre-trade R:R gate) -----------------------
    # Cripto perps slippage is heavier than equities — R:R 1.5 nominal often
    # collapses to 1.0-1.2 effective. Forced buffer per symbol raises the
    # required nominal R:R so the post-slippage realized one stays above 1.5.
    # Defaults calibrated from p75 of observed bid-ask spread on Binance
    # USDT-M Q1 2026 (preliminary — will be recalibrated from paper_fills
    # once enough trades are in).
    slippage_buffer_r_btcusdt: float = Field(default=0.3, alias="SLIPPAGE_BUFFER_R_BTCUSDT")
    slippage_buffer_r_ethusdt: float = Field(default=0.3, alias="SLIPPAGE_BUFFER_R_ETHUSDT")
    slippage_buffer_r_solusdt: float = Field(default=0.4, alias="SLIPPAGE_BUFFER_R_SOLUSDT")
    slippage_buffer_r_bnbusdt: float = Field(default=0.4, alias="SLIPPAGE_BUFFER_R_BNBUSDT")
    # Fallback for any other symbol (more conservative — unknown liquidity).
    slippage_buffer_r_default: float = Field(default=0.5, alias="SLIPPAGE_BUFFER_R_DEFAULT")

    # ---- B.1 — Deterministic risk manager --------------------------------
    # Master switch. Default ON since the rules are conservative and only
    # tighten the stop (never widen risk). The runtime still validates
    # idempotency via risk_state so re-runs are safe.
    risk_manager_enabled: bool = Field(default=True, alias="RISK_MANAGER_ENABLED")
    # Unrealized R threshold at which BE move triggers. 0.5R is a common
    # default: half the initial risk locked in → asymmetric profile.
    risk_move_to_be_after_r: float = Field(default=0.5, alias="RISK_MOVE_TO_BE_AFTER_R")
    # ATR multiple used for trailing stop placement after TP1.
    risk_trailing_atr_multiple: float = Field(
        default=2.0, alias="RISK_TRAILING_ATR_MULTIPLE"
    )
    # Per-timeframe max hold (hours). After the deadline the setup closes at
    # market (current close). Calibrated so a trade in motion gets reasonable
    # room while a forgotten trade doesn't bleed funding indefinitely.
    risk_max_hold_hours_15m: int = Field(default=12, alias="RISK_MAX_HOLD_HOURS_15M")
    risk_max_hold_hours_1h: int = Field(default=24, alias="RISK_MAX_HOLD_HOURS_1H")
    risk_max_hold_hours_4h: int = Field(default=72, alias="RISK_MAX_HOLD_HOURS_4H")
    risk_max_hold_hours_1d: int = Field(default=240, alias="RISK_MAX_HOLD_HOURS_1D")

    # ---- A.4 — Market dominance (CoinGecko) ------------------------------
    # CoinGecko free tier supports unauthenticated /api/v3/global; supplying
    # COINGECKO_API_KEY (demo key) raises the rate limit. Both env names
    # commonly used by clients are accepted.
    coingecko_base_url: str = Field(
        default="https://api.coingecko.com",
        alias="COINGECKO_BASE_URL",
    )
    coingecko_api_key: str | None = Field(default=None, alias="COINGECKO_API_KEY")
    # Cache TTL del snapshot live en Redis. 15min cubre la latencia natural
    # del dato (CoinGecko refreshea cada ~5min) sin saturar el endpoint
    # cuando muchas chat turns consultan al mismo tiempo.
    dominance_cache_ttl_seconds: int = Field(default=900, alias="DOMINANCE_CACHE_TTL_SECONDS")

    @property
    def review_time_offsets_list(self) -> tuple[int, ...]:
        out: list[int] = []
        for tok in self.review_time_offsets_h.split(","):
            tok = tok.strip()
            if tok:
                try:
                    out.append(int(tok))
                except ValueError:
                    continue
        return tuple(sorted(set(out)))

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def watch_symbol_list(self) -> list[str]:
        return [s.strip().upper() for s in self.watch_symbols.split(",") if s.strip()]

    def slippage_buffer_r(self, symbol: str) -> float:
        """Per-symbol slippage buffer for the R:R pre-trade gate.

        Looks up the symbol-specific field; falls back to `_default` for
        unlisted symbols. Symbols are uppercased before lookup.
        """
        s = symbol.upper()
        mapping: dict[str, float] = {
            "BTCUSDT": self.slippage_buffer_r_btcusdt,
            "ETHUSDT": self.slippage_buffer_r_ethusdt,
            "SOLUSDT": self.slippage_buffer_r_solusdt,
            "BNBUSDT": self.slippage_buffer_r_bnbusdt,
        }
        return mapping.get(s, self.slippage_buffer_r_default)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
