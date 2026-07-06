"""Langfuse observability bootstrap.

Configures the Langfuse SDK from settings. With no keys configured (the
default), tracing is explicitly disabled so every `@observe()` decorator in
the codebase becomes a silent no-op — unit tests and local runs without
Langfuse behave exactly as before.
"""

import threading

from langfuse.decorators import langfuse_context

from core.config import Settings, get_settings
from core.logging import get_logger

log = get_logger(__name__)

# Hard ceiling on shutdown flush. langfuse_context.flush() is blocking with no
# timeout parameter, so if the Langfuse backend is unreachable its consumer
# thread can hang indefinitely — and this runs on ARQ worker shutdown, blocking
# restarts and deploys. We bound it and move on; a few dropped traces are an
# acceptable price for a worker that always shuts down.
_FLUSH_TIMEOUT_SECONDS = 5.0


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
    """Deliver buffered traces now (the SDK batches and sends asynchronously).

    Bounded by `_FLUSH_TIMEOUT_SECONDS`: the flush runs in a daemon thread so a
    hung/unreachable Langfuse backend can't stall worker shutdown indefinitely.
    """
    flusher = threading.Thread(target=langfuse_context.flush, daemon=True)
    flusher.start()
    flusher.join(timeout=_FLUSH_TIMEOUT_SECONDS)
    if flusher.is_alive():
        log.warning("langfuse_flush_timeout", timeout_seconds=_FLUSH_TIMEOUT_SECONDS)
