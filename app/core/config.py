from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Environment variables take precedence; .env is read for local development.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "event-booking"
    database_url: str  # required: startup fails if missing
    redis_url: str

    # PRD Section 8 defaults. Changes apply only to new allocations.
    hold_ttl_seconds: int = 300
    offer_ttl_seconds: int = 600

    # DATABASE_DESIGN Section 4: per-connection timeouts.
    db_lock_timeout: str = "5s"
    db_statement_timeout: str = "10s"
    db_idle_in_transaction_timeout: str = "15s"
    db_pool_size: int = 10
    db_max_overflow: int = 20


@lru_cache
def get_settings() -> Settings:
    return Settings()
