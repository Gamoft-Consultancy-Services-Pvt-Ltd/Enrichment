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

    # Auth0 (managed auth provider). Empty defaults keep tests/local imports working.
    auth0_domain: str = ""
    auth0_audience: str = ""
    auth0_algorithms: list[str] = ["RS256"]
    auth_claim_namespace: str = "https://leadengine/"
    # SPA Client ID used only so the /docs "Authorize" button can run the Auth0 login.
    auth0_spa_client_id: str = ""


@lru_cache
def get_settings() -> Settings:
    """Return the single, cached Settings instance (parsed once per process)."""
    return Settings()
