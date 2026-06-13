"""ARQ cron job: daily refresh of long-lived Instagram tokens."""

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.config import get_settings
from modules.lead_ingestion.instagram_token_refresh import refresh_instagram_tokens


async def run_instagram_token_refresh(ctx: dict[str, object]) -> None:
    """Create a DB session and refresh all active Instagram tokens near expiry."""
    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await refresh_instagram_tokens(session)
    await engine.dispose()
