"""Tests for core.config — the minimal application settings."""

import pytest

from core.config import get_settings
from tests.helpers import build_settings


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no environment overrides, settings fall back to safe defaults."""
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    settings = build_settings()

    assert settings.env == "development"
    assert settings.log_level == "INFO"


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment variables override the defaults."""
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    settings = build_settings()

    assert settings.env == "development"
    assert settings.log_level == "DEBUG"


def test_production_settings_require_secrets() -> None:
    """Settings with env=production raises ValueError when required secrets are absent."""
    with pytest.raises(ValueError, match="Required secrets not set"):
        build_settings(env="production")


def test_production_settings_boot_with_all_secrets() -> None:
    """Settings with env=production succeeds when all required secrets are provided."""
    settings = build_settings(
        env="production",
        groq_api_key="gsk_test",
        channel_credentials_encryption_key="key",
        meta_app_secret="secret",
        auth0_domain="acme.us.auth0.com",
        auth0_audience="api://leadengine",
        meta_webhook_verify_token="token",
        auth0_mgmt_client_id="mgmt_client_id",
        auth0_mgmt_client_secret="mgmt_client_secret",
    )
    assert settings.env == "production"


def test_staging_settings_require_secrets() -> None:
    """env=staging is treated the same as production for secret validation."""
    with pytest.raises(ValueError, match="Required secrets not set"):
        build_settings(env="staging")


def test_settings_database_url_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """database_url defaults to a local async Postgres DSN."""
    monkeypatch.delenv("DATABASE_URL", raising=False)

    settings = build_settings()

    assert settings.database_url.startswith("postgresql+asyncpg://")


def test_settings_database_url_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """DATABASE_URL from the environment overrides the default."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db:5432/test")

    settings = build_settings()

    assert settings.database_url == "postgresql+asyncpg://u:p@db:5432/test"


def test_get_settings_is_cached() -> None:
    """get_settings returns the same instance every call (parsed once)."""
    assert get_settings() is get_settings()


def test_auth0_settings_have_sensible_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auth0 settings default to empty domain/audience, RS256, the claim namespace."""
    monkeypatch.delenv("AUTH0_DOMAIN", raising=False)
    monkeypatch.delenv("AUTH0_AUDIENCE", raising=False)
    monkeypatch.delenv("AUTH0_ALGORITHMS", raising=False)
    monkeypatch.delenv("AUTH_CLAIM_NAMESPACE", raising=False)

    settings = build_settings()

    assert settings.auth0_domain == ""
    assert settings.auth0_audience == ""
    assert settings.auth0_algorithms == ["RS256"]
    assert settings.auth_claim_namespace == "https://leadengine/"


def test_auth0_settings_are_overridable() -> None:
    """Auth0 domain/audience can be overridden (here via direct construction)."""
    settings = build_settings(auth0_domain="acme.us.auth0.com", auth0_audience="api://leadengine")

    assert settings.auth0_domain == "acme.us.auth0.com"
    assert settings.auth0_audience == "api://leadengine"


def test_settings_has_serper_api_key() -> None:
    settings = build_settings(serper_api_key="test-key")
    assert settings.serper_api_key == "test-key"


def test_settings_serper_api_key_defaults_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    settings = build_settings()
    assert settings.serper_api_key == ""


def test_pan_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """PAN verification defaults to the mock path with no credentials."""
    for var in ("PAN_USE_MOCK", "PAN_API_KEY", "PAN_API_SECRET", "PAN_BASE_URL", "PAN_TEST_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    settings = build_settings()
    assert settings.pan_use_mock is True
    assert settings.pan_api_key == ""
    assert settings.pan_api_secret == ""
    assert settings.pan_base_url == "https://api.sandbox.co.in"
    assert settings.pan_test_base_url == "https://test-api.sandbox.co.in"


def test_settings_langfuse_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Langfuse keys default to empty (tracing disabled); host to the Docker service."""
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)

    settings = build_settings()

    assert settings.langfuse_public_key == ""
    assert settings.langfuse_secret_key == ""
    assert settings.langfuse_host == "http://langfuse:3000"


def test_settings_langfuse_overridable() -> None:
    settings = build_settings(
        langfuse_public_key="pk-lf-test",
        langfuse_secret_key="sk-lf-test",
        langfuse_host="http://localhost:3000",
    )

    assert settings.langfuse_public_key == "pk-lf-test"
    assert settings.langfuse_secret_key == "sk-lf-test"
    assert settings.langfuse_host == "http://localhost:3000"


def test_mcp_web_search_url_default_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset means unset: no plausible-but-wrong default that silently 404s.

    The old default (http://localhost:8000/mcp) is the app's own port, so an
    unconfigured run reached FastAPI instead of the MCP server and failed opaquely.
    """
    monkeypatch.delenv("MCP_WEB_SEARCH_URL", raising=False)
    settings = build_settings()
    assert settings.mcp_web_search_url == ""


def test_mcp_web_search_url_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_WEB_SEARCH_URL", "http://mcp-web-search:8000/mcp")
    settings = build_settings()
    assert settings.mcp_web_search_url == "http://mcp-web-search:8000/mcp"


def test_openrouter_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    settings = build_settings()
    assert settings.openrouter_api_key == ""
    assert settings.openrouter_base_url == "https://openrouter.ai/api/v1"


def test_openrouter_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://example.test/v1")
    settings = build_settings()
    assert settings.openrouter_api_key == "sk-or-test"
    assert settings.openrouter_base_url == "https://example.test/v1"


def test_pan_effective_base_url_uses_free_test_api_in_dev() -> None:
    settings = build_settings(env="development", pan_base_url="https://api.sandbox.co.in")
    assert settings.pan_effective_base_url == "https://test-api.sandbox.co.in"


def test_pan_effective_base_url_uses_billed_api_in_production() -> None:
    settings = build_settings(
        env="production",
        groq_api_key="gsk_test",
        channel_credentials_encryption_key="key",
        meta_app_secret="secret",
        auth0_domain="acme.us.auth0.com",
        auth0_audience="api://leadengine",
        meta_webhook_verify_token="token",
        auth0_mgmt_client_id="mgmt_client_id",
        auth0_mgmt_client_secret="mgmt_client_secret",
        pan_base_url="https://api.sandbox.co.in",
    )
    assert settings.pan_effective_base_url == "https://api.sandbox.co.in"


def test_pan_test_credentials_default_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("PAN_TEST_API_KEY", "PAN_TEST_API_SECRET"):
        monkeypatch.delenv(var, raising=False)
    settings = build_settings()
    assert settings.pan_test_api_key == ""
    assert settings.pan_test_api_secret == ""


def test_pan_effective_credentials_use_test_keys_in_dev() -> None:
    """Non-prod envs hit the free test host, so the effective creds are the test pair."""
    settings = build_settings(
        env="development",
        pan_api_key="key_live_x",
        pan_api_secret="secret_live_x",
        pan_test_api_key="key_test_x",
        pan_test_api_secret="secret_test_x",
    )
    assert settings.pan_effective_api_key == "key_test_x"
    assert settings.pan_effective_api_secret == "secret_test_x"


def test_pan_effective_credentials_use_live_keys_in_production() -> None:
    """Production hits the billed host, so the effective creds are the live pair."""
    settings = build_settings(
        env="production",
        groq_api_key="gsk_test",
        channel_credentials_encryption_key="key",
        meta_app_secret="secret",
        auth0_domain="acme.us.auth0.com",
        auth0_audience="api://leadengine",
        meta_webhook_verify_token="token",
        auth0_mgmt_client_id="mgmt_client_id",
        auth0_mgmt_client_secret="mgmt_client_secret",
        pan_api_key="key_live_x",
        pan_api_secret="secret_live_x",
        pan_test_api_key="key_test_x",
        pan_test_api_secret="secret_test_x",
    )
    assert settings.pan_effective_api_key == "key_live_x"
    assert settings.pan_effective_api_secret == "secret_live_x"
