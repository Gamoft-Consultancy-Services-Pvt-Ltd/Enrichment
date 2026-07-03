"""ARQ cron job: daily refresh of long-lived Instagram tokens."""

from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from modules.lead_ingestion.instagram_token_refresh import refresh_instagram_tokens


async def run_instagram_token_refresh(ctx: dict[str, object]) -> None:
    """Refresh all active Instagram tokens near expiry using the shared worker engine."""
    factory = cast(async_sessionmaker[AsyncSession], ctx["session_factory"])
    async with factory() as session:
        await refresh_instagram_tokens(session)
