"""Tests for core.lifespan — application startup/shutdown hooks."""

from unittest.mock import AsyncMock

import pytest
import structlog
from fastapi import FastAPI

from core.lifespan import lifespan


@pytest.fixture(autouse=True)
def mock_arq_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_pool = AsyncMock()
    monkeypatch.setattr("core.lifespan.create_arq_pool", AsyncMock(return_value=mock_pool))


async def test_lifespan_configures_logging() -> None:
    """Entering the app lifespan configures structured logging."""
    structlog.reset_defaults()
    assert structlog.is_configured() is False

    async with lifespan(FastAPI()):
        assert structlog.is_configured() is True
