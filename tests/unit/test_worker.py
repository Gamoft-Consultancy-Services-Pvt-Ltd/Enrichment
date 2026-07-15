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


def test_job_timeout_clears_the_onboarding_pipeline_worst_case() -> None:
    """ARQ's 300s default is too tight for onboarding, whose research loop can run long.

    A real converging run took ~195s; a non-converging one walks all 25 ReAct
    iterations before falling back. Inheriting the default would kill those runs
    mid-pipeline, which surfaces as a hang rather than a clean failure.
    """
    assert WorkerSettings.job_timeout > 300
