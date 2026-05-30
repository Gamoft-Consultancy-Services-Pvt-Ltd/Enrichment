"""Reusable test helpers."""

from typing import Any

from core.config import Settings


def build_settings(**overrides: Any) -> Settings:
    """Build Settings for tests, ignoring any local .env so results are deterministic.

    pydantic-settings accepts the _env_file kwarg at runtime, but its mypy plugin
    does not model it; the ignore is centralised here so call sites stay clean.
    """
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]
