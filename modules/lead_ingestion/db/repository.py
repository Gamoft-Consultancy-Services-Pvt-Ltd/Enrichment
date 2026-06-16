"""Async data access layer for lead_ingestion models."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.lead_ingestion.db.models import IntakeEventLog, Lead
from shared.channels.models import ChannelConnection


async def get_lead_by_phone(
    session: AsyncSession, tenant_id: uuid.UUID, phone_normalised: str
) -> Lead | None:
    result = await session.execute(
        select(Lead).where(
            Lead.tenant_id == tenant_id,
            Lead.phone == phone_normalised,
        )
    )
    return result.scalar_one_or_none()


async def get_lead_by_email(session: AsyncSession, tenant_id: uuid.UUID, email: str) -> Lead | None:
    result = await session.execute(
        select(Lead).where(
            Lead.tenant_id == tenant_id,
            Lead.email == email,
        )
    )
    return result.scalar_one_or_none()


async def get_lead_by_name_and_location(
    session: AsyncSession, tenant_id: uuid.UUID, full_name: str, location: str
) -> Lead | None:
    result = await session.execute(
        select(Lead).where(
            Lead.tenant_id == tenant_id,
            Lead.full_name == full_name,
            Lead.location == location,
        )
    )
    return result.scalar_one_or_none()


async def get_lead_by_platform_event_id(
    session: AsyncSession, platform_event_id: str
) -> Lead | None:
    """Join leads through intake_event_logs to find a lead by its source event."""
    result = await session.execute(
        select(Lead)
        .join(IntakeEventLog, IntakeEventLog.lead_id == Lead.id)
        .where(IntakeEventLog.platform_event_id == platform_event_id)
    )
    return result.scalar_one_or_none()


async def get_channel_connection(
    session: AsyncSession, connection_id: uuid.UUID
) -> ChannelConnection | None:
    result = await session.execute(
        select(ChannelConnection).where(ChannelConnection.id == connection_id)
    )
    return result.scalar_one_or_none()


async def get_whatsapp_connection_by_phone_number_id(
    session: AsyncSession, phone_number_id: str
) -> ChannelConnection | None:
    """Find an active WhatsApp ChannelConnection by its Meta phone_number_id.

    The phone_number_id is stored inside connection_metadata as a JSON string.
    """
    result = await session.execute(
        select(ChannelConnection).where(
            ChannelConnection.channel_type == "whatsapp",
            ChannelConnection.status == "active",
            ChannelConnection.connection_metadata["phone_number_id"].astext == phone_number_id,
        )
    )
    return result.scalar_one_or_none()


async def log_unroutable_event(
    session: AsyncSession,
    platform_event_id: str,
    source_channel: str,
    raw_event_json: dict[str, Any],
) -> None:
    """Write an IntakeEventLog row for a webhook that could not be routed to any tenant."""
    log = IntakeEventLog(
        tenant_id=None,
        lead_id=None,
        platform_event_id=platform_event_id,
        source_channel=source_channel,
        status="unroutable",
        raw_event_json=raw_event_json,
    )
    session.add(log)
    await session.commit()


async def get_connection_by_page_or_ig_account_id(
    session: AsyncSession, account_id: str
) -> ChannelConnection | None:
    """Find an active Facebook or Instagram ChannelConnection by page_id or ig_account_id.

    Used to route 'page' (Facebook DMs + Lead Ads) and 'instagram' webhook events.
    The IDs are stored inside connection_metadata; a GIN index on that column
    makes this lookup efficient.
    """
    result = await session.execute(
        select(ChannelConnection).where(
            ChannelConnection.status == "active",
            ChannelConnection.channel_type.in_(["facebook", "instagram"]),
            (ChannelConnection.connection_metadata["page_id"].astext == account_id)
            | (ChannelConnection.connection_metadata["ig_account_id"].astext == account_id),
        )
    )
    return result.scalar_one_or_none()
