"""ARQ job: drive a received lead through the enrichment pipeline."""

from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from modules.orchestration.service import process_lead
from shared.events.schemas import LeadReceived


async def run_lead_pipeline(ctx: dict[str, object], event_dict: dict[str, Any]) -> None:
    """Rebuild the LeadReceived, enrich the lead, and persist the result."""
    event = LeadReceived.model_validate(event_dict)
    factory = cast(async_sessionmaker[AsyncSession], ctx["session_factory"])
    async with factory() as session:
        await process_lead(session, event)
        await session.commit()
