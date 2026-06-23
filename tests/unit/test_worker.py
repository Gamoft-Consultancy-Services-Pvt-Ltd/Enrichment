"""Unit tests for workers/worker — ARQ settings wiring, no Redis needed."""

from unittest.mock import patch

from workers.worker import WorkerSettings, shutdown, startup


def test_worker_registers_langfuse_lifecycle_hooks() -> None:
    """Configure tracing when the worker starts; flush buffered traces on shutdown."""
    assert WorkerSettings.on_startup is startup
    assert WorkerSettings.on_shutdown is shutdown


async def test_startup_configures_langfuse() -> None:
    with patch("workers.worker.configure_langfuse") as mock_configure:
        await startup({})

    mock_configure.assert_called_once_with()


async def test_shutdown_flushes_langfuse() -> None:
    with patch("workers.worker.flush_langfuse") as mock_flush:
        await shutdown({})

    mock_flush.assert_called_once_with()
