"""Tests for core.lifespan — application startup/shutdown hooks."""

import structlog
from fastapi import FastAPI

from core.lifespan import lifespan


async def test_lifespan_configures_logging() -> None:
    """Entering the app lifespan configures structured logging."""
    structlog.reset_defaults()
    assert structlog.is_configured() is False

    async with lifespan(FastAPI()):
        assert structlog.is_configured() is True
