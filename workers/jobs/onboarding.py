"""ARQ job: run the full onboarding pipeline for a tenant."""

from typing import cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from modules.tenant_onboarding.pipeline import run_pipeline


async def run_onboarding_pipeline(ctx: dict[str, object], tenant_id: str) -> None:
    """Fetch the tenant's website and run persona → ICP → signals pipeline."""
    factory = cast(async_sessionmaker[AsyncSession], ctx["session_factory"])
    async with factory() as session:
        await run_pipeline(session, UUID(tenant_id))
