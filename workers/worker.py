"""ARQ worker entry point — registers all background jobs and cron schedules."""

from typing import cast

from arq.connections import RedisSettings
from arq.cron import cron
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from core.config import get_settings
from workers.jobs.instagram_refresh import run_instagram_token_refresh
from workers.jobs.lead_ingestion import (
    run_lead_ad_capture,
    run_lead_capture,
    run_lead_capture_batch,
)
from workers.jobs.onboarding import run_onboarding_pipeline


async def _on_startup(ctx: dict[str, object]) -> None:
    engine: AsyncEngine = create_async_engine(get_settings().database_url)
    ctx["engine"] = engine
    ctx["session_factory"] = async_sessionmaker(engine, expire_on_commit=False)


async def _on_shutdown(ctx: dict[str, object]) -> None:
    engine = cast(AsyncEngine, ctx.get("engine"))
    if engine is not None:
        await engine.dispose()


class WorkerSettings:
    """ARQ worker configuration. Run with: uv run arq workers.worker.WorkerSettings"""

    on_startup = _on_startup
    on_shutdown = _on_shutdown
    max_tries = 2  # 1 retry on failure; dead-letters after 2nd failure
    functions = [run_onboarding_pipeline, run_lead_capture_batch, run_lead_capture, run_lead_ad_capture]
    cron_jobs = [cron(run_instagram_token_refresh, hour=2, minute=0)]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
