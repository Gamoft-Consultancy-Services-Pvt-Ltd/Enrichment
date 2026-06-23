"""ARQ worker entry point — registers all background jobs."""

from typing import Any

from arq.connections import RedisSettings

from core.config import get_settings
from core.observability import configure_langfuse, flush_langfuse
from workers.jobs.onboarding import run_onboarding_pipeline


async def startup(ctx: dict[str, Any]) -> None:
    """Configure Langfuse tracing for this worker process."""
    configure_langfuse()


async def shutdown(ctx: dict[str, Any]) -> None:
    """Flush buffered Langfuse traces before the worker exits."""
    flush_langfuse()


class WorkerSettings:
    """ARQ worker configuration. Run with: uv run arq workers.worker.WorkerSettings"""

    functions = [run_onboarding_pipeline]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
