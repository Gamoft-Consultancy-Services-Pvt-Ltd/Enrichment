"""Shared pytest fixtures and configuration."""

import pytest


@pytest.fixture
def sample_fixture() -> str:
    """Placeholder fixture to prove the test setup works."""
    return "ok"
