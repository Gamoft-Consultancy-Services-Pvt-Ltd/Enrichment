"""Application settings.

A single source of truth for configuration, read from environment variables
(and an optional .env file). This is the seed of config — other modules add
their own settings here as the application grows.
"""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REQUIRED_IN_PRODUCTION = [
    "groq_api_key",
    "channel_credentials_encryption_key",
    "meta_app_secret",
    "auth0_domain",
    "auth0_audience",
    "meta_webhook_verify_token",
    "auth0_mgmt_client_id",
    "auth0_mgmt_client_secret",
]


class Settings(BaseSettings):
    """Strongly-typed application settings, loaded from the environment."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/leadengine"

    redis_url: str = "redis://localhost:6379"
    groq_api_key: str = ""
    serper_api_key: str = ""
    mcp_web_search_url: str = "http://localhost:8000/mcp"

    # Lead ingestion — channel credentials and Meta webhook. Empty defaults keep
    # tests/local imports working (mirrors Auth0 field pattern).
    channel_credentials_encryption_key: str = ""
    meta_app_id: str = ""
    meta_app_secret: str = ""
    # Instagram Business Login uses a separate linked app with its own credentials.
    meta_ig_app_id: str = ""
    meta_ig_app_secret: str = ""
    meta_webhook_verify_token: str = ""
    meta_graph_api_version: str = "v21.0"
    meta_embedded_signup_config_id: str = ""
    # Public base URL — used to build OAuth redirect URIs. Set to ngrok URL in dev.
    base_url: str = "http://localhost:8000"

    # Langfuse (self-hosted AI observability). Empty keys disable tracing entirely.
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://langfuse:3000"

    # PAN KYB verification via Sandbox/Quicko (clients/pan_client.py). Mock path is
    # on by default so onboarding runs with no provider credentials; flip
    # pan_use_mock off and set the Sandbox key/secret + base URL once available.
    # pan_base_url: https://test-api.sandbox.co.in (sandbox) or https://api.sandbox.co.in.
    pan_api_key: str = ""
    pan_api_secret: str = ""
    pan_base_url: str = ""
    pan_use_mock: bool = True

    # Auth0 (managed auth provider). Empty defaults keep tests/local imports working.
    auth0_domain: str = ""
    auth0_audience: str = ""
    auth0_algorithms: list[str] = ["RS256"]
    auth_claim_namespace: str = "https://leadengine/"
    # SPA Client ID used only so the /docs "Authorize" button can run the Auth0 login.
    auth0_spa_client_id: str = ""
    # Management API credentials — used by onboarding to update user app_metadata after
    # tenant creation, so the next JWT carries role=TENANT and the correct tenant_id.
    auth0_mgmt_client_id: str = ""
    auth0_mgmt_client_secret: str = ""

    # Data lifecycle retention windows (COMP-302)
    lead_data_retention_days: int = 730
    intake_log_retention_days: int = 180

    @model_validator(mode="after")
    def _validate_production_secrets(self) -> "Settings":
        if self.env not in ("production", "staging"):
            return self
        missing = [f for f in _REQUIRED_IN_PRODUCTION if not getattr(self, f)]
        if missing:
            raise ValueError(f"Required secrets not set for env='{self.env}': {', '.join(missing)}")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the single, cached Settings instance (parsed once per process)."""
    return Settings()
