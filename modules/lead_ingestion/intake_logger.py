"""IntakeEventLog writer with ON CONFLICT (platform_event_id) DO NOTHING semantics."""

from uuid import UUID, uuid4

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from modules.lead_ingestion.db.models import IntakeEventLog
from modules.lead_ingestion.schemas.normalised_event import NormalisedChannelEvent


async def reserve_event_slot(
    session: AsyncSession,
    event: NormalisedChannelEvent,
) -> UUID | None:
    """Claim a platform_event_id slot atomically.

    Inserts an IntakeEventLog row with lead_id=NULL and status='pending'.
    Returns the new row's id, or None if the platform_event_id was already
    present (ON CONFLICT DO NOTHING — redelivery detected).

    Using RETURNING instead of rowcount because asyncpg's rowcount is
    unreliable for ON CONFLICT DO NOTHING statements.
    """
    stmt = (
        pg_insert(IntakeEventLog)
        .values(
            id=uuid4(),
            tenant_id=event.tenant_id,
            lead_id=None,
            platform_event_id=event.platform_event_id,
            source_channel=event.source.value,
            status="pending",
            raw_event_json=event.raw_event_json,
        )
        .on_conflict_do_nothing(index_elements=["platform_event_id"])
        .returning(IntakeEventLog.id)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    return UUID(str(row)) if row is not None else None


async def finalise_event_log(
    session: AsyncSession,
    log_id: UUID,
    lead_id: UUID,
    status: str,
) -> None:
    """Set lead_id and the final status on the reserved log row."""
    await session.execute(
        update(IntakeEventLog)
        .where(IntakeEventLog.id == log_id)
        .values(lead_id=lead_id, status=status)
    )
