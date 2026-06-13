"""ARQ worker entry point — registers all background jobs and cron schedules."""

from arq.connections import RedisSettings
from arq.cron import cron

from core.config import get_settings
from workers.jobs.instagram_refresh import run_instagram_token_refresh
from workers.jobs.lead_ingestion import run_lead_capture, run_lead_capture_batch
from workers.jobs.onboarding import run_onboarding_pipeline


class WorkerSettings:
    """ARQ worker configuration. Run with: uv run arq workers.worker.WorkerSettings"""

    functions = [run_onboarding_pipeline, run_lead_capture_batch, run_lead_capture]
    # Refresh Instagram tokens daily at 02:00 UTC (well before the 7-day expiry window)
    cron_jobs = [cron(run_instagram_token_refresh, hour=2, minute=0)]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
