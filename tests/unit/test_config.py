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
