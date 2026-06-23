"""Unit tests for core/observability — Langfuse SDK wiring, no network."""

from unittest.mock import patch

from core.observability import configure_langfuse, flush_langfuse
from tests.helpers import build_settings


def test_configure_disables_tracing_when_keys_missing() -> None:
    """Empty keys (the default) must explicitly disable the SDK — silent no-op."""
    settings = build_settings(langfuse_public_key="", langfuse_secret_key="")

    with patch("core.observability.langfuse_context") as mock_ctx:
        configure_langfuse(settings)

    mock_ctx.configure.assert_called_once_with(enabled=False)


def test_configure_enables_tracing_when_keys_present() -> None:
    settings = build_settings(
        langfuse_public_key="pk-lf-test",
        langfuse_secret_key="sk-lf-test",
        langfuse_host="http://langfuse:3000",
    )

    with patch("core.observability.langfuse_context") as mock_ctx:
        configure_langfuse(settings)

    mock_ctx.configure.assert_called_once_with(
        public_key="pk-lf-test",
        secret_key="sk-lf-test",
        host="http://langfuse:3000",
        enabled=True,
    )


def test_flush_delegates_to_sdk() -> None:
    with patch("core.observability.langfuse_context") as mock_ctx:
        flush_langfuse()

    mock_ctx.flush.assert_called_once_with()
