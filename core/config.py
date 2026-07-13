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

    # OpenRouter serves the project's LLM (Qwen3-8B). OpenRouter speaks the OpenAI
    # API protocol, so it is called with the OpenAI-compatible client pointed here.
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

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
    # pan_use_mock off and set the Sandbox key/secret once available.
    # Two live endpoints share the same credentials: production is BILLED per call,
    # the test endpoint is FREE. `pan_effective_base_url` picks the test endpoint in
    # non-production envs so development and tests never cost money.
    # The test host requires its own key_test_/secret_test_ credentials; the live
    # host requires key_live_/secret_live_. `pan_effective_api_*` picks the pair that
    # matches `pan_effective_base_url` for the current env.
    pan_api_key: str = ""
    pan_api_secret: str = ""
    pan_test_api_key: str = ""
    pan_test_api_secret: str = ""
    pan_base_url: str = "https://api.sandbox.co.in"  # production (billed)
    pan_test_base_url: str = "https://test-api.sandbox.co.in"  # test (free, dev)
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

    @property
    def pan_effective_base_url(self) -> str:
        """Sandbox PAN base URL for the current env.

        Production/staging use the billed production endpoint; every other env
        (development, local, test) uses the free test endpoint so onboarding runs
        during development do not cost money.
        """
        if self.env in ("production", "staging"):
            return self.pan_base_url
        return self.pan_test_base_url

    @property
    def pan_effective_api_key(self) -> str:
        """Sandbox API key for the current env — must match `pan_effective_base_url`."""
        if self.env in ("production", "staging"):
            return self.pan_api_key
        return self.pan_test_api_key

    @property
    def pan_effective_api_secret(self) -> str:
        """Sandbox API secret for the current env — must match `pan_effective_base_url`."""
        if self.env in ("production", "staging"):
            return self.pan_api_secret
        return self.pan_test_api_secret

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
