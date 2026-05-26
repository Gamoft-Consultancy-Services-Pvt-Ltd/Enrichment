"""Smoke test to prove the test runner and CI pipeline are wired correctly."""


def test_smoke() -> None:
    """The most basic possible test. If this fails, something is fundamentally broken."""
    assert True


def test_python_version() -> None:
    """Confirm we are running on the expected Python version."""
    import sys

    assert sys.version_info >= (3, 13)
