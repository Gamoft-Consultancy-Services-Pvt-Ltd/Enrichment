"""Application settings.

A single source of truth for configuration, read from environment variables
(and an optional .env file). This is the seed of config — other modules add
their own settings here as the application grows.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly-typed application settings, loaded from the environment."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/leadengine"


@lru_cache
def get_settings() -> Settings:
    """Return the single, cached Settings instance (parsed once per process)."""
    return Settings()
