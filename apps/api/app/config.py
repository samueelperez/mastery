from functools import lru_cache

from pydantic import Field
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

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def watch_symbol_list(self) -> list[str]:
        return [s.strip().upper() for s in self.watch_symbols.split(",") if s.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
