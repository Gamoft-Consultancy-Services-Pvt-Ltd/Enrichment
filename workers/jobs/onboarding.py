"""ARQ job: run the full onboarding pipeline for a tenant."""

from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.config import get_settings
from modules.tenant_onboarding.pipeline import run_pipeline


async def run_onboarding_pipeline(ctx: dict[str, object], tenant_id: str) -> None:
    """Fetch the tenant's website and run persona → ICP → signals pipeline."""
    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await run_pipeline(session, UUID(tenant_id))
    await engine.dispose()
