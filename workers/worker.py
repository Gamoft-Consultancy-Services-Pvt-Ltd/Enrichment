"""ARQ worker entry point — registers all background jobs."""

from arq.connections import RedisSettings

from core.config import get_settings
from workers.jobs.onboarding import run_onboarding_pipeline


class WorkerSettings:
    """ARQ worker configuration. Run with: uv run arq workers.worker.WorkerSettings"""

    functions = [run_onboarding_pipeline]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
