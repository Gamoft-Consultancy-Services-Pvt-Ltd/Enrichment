"""ARQ cron job: daily anonymisation sweep for leads past their retention window."""

from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from modules.lead_ingestion.retention import run_retention_sweep


async def run_lead_retention_sweep(ctx: dict[str, object]) -> None:
    """Anonymise all leads and event logs that have exceeded their retention window."""
    factory = cast(async_sessionmaker[AsyncSession], ctx["session_factory"])
    async with factory() as session:
        await run_retention_sweep(session)
