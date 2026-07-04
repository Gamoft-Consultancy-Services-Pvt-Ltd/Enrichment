"""Langfuse observability bootstrap.

Configures the Langfuse SDK from settings. With no keys configured (the
default), tracing is explicitly disabled so every `@observe()` decorator in
the codebase becomes a silent no-op — unit tests and local runs without
Langfuse behave exactly as before.
"""

from langfuse.decorators import langfuse_context

from core.config import Settings, get_settings


def configure_langfuse(settings: Settings | None = None) -> None:
    """Enable Langfuse tracing if keys are configured, otherwise disable it."""
    settings = settings or get_settings()
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        langfuse_context.configure(enabled=False)
        return
    langfuse_context.configure(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
        enabled=True,
    )


def flush_langfuse() -> None:
    """Deliver buffered traces now (the SDK batches and sends asynchronously)."""
    langfuse_context.flush()
