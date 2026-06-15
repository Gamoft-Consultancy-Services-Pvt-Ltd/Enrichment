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
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    settings = build_settings()

    assert settings.env == "production"
    assert settings.log_level == "DEBUG"


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


def test_auth0_settings_have_sensible_defaults() -> None:
    """Auth0 settings default to empty domain/audience, RS256, the claim namespace."""
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


def test_surepass_settings_have_defaults() -> None:
    settings = build_settings()
    assert settings.surepass_api_key == ""
    assert settings.surepass_base_url == "https://kyc-api.surepass.io"
    assert settings.surepass_use_mock is True
