"""ARQ jobs for lead ingestion: file-upload batch, message-based webhook, and Lead Ads events."""

from typing import Any, cast
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clients.meta_leads_client import fetch_lead_fields
from core.config import get_settings
from core.exceptions import ExternalServiceError
from modules.lead_ingestion.service import (
    Lead,
    PreFlightHaltError,
    check_pre_flight,
    decrypt_credentials,
    get_channel_connection,
    normalise_facebook_dm,
    normalise_file_row,
    normalise_instagram_dm,
    normalise_lead_ad_form,
    normalise_whatsapp_message,
    run_capture,
    run_capture_message,
)
from shared.events.schemas import LeadSource

log = structlog.get_logger()


async def run_lead_capture(ctx: dict[str, object], payload_dict: dict[str, Any]) -> None:
    """Process a single message-based webhook event (WhatsApp, Facebook, Instagram)."""
    tenant_id = UUID(cast(str, payload_dict["tenant_id"]))
    channel_connection_id_str = cast(str | None, payload_dict.get("channel_connection_id"))
    channel_connection_id = UUID(channel_connection_id_str) if channel_connection_id_str else None
    raw_payload: dict[str, Any] = cast(dict[str, Any], payload_dict["raw_payload"])

    factory = cast(async_sessionmaker[AsyncSession], ctx["session_factory"])
    async with factory() as session:
        object_type = raw_payload.get("object", "")
        if object_type == "whatsapp_business_account":
            value: dict[str, Any] = cast(
                dict[str, Any], raw_payload["entry"][0]["changes"][0]["value"]
            )
            if "messages" not in value:
                return
            event = normalise_whatsapp_message(
                raw_payload,
                tenant_id=tenant_id,
                channel_connection_id=channel_connection_id,
            )
        elif object_type == "page":
            event = normalise_facebook_dm(
                raw_payload,
                tenant_id=tenant_id,
                channel_connection_id=channel_connection_id,
            )
        elif object_type == "instagram":
            event = normalise_instagram_dm(
                raw_payload,
                tenant_id=tenant_id,
                channel_connection_id=channel_connection_id,
            )
        else:
            return
        await run_capture_message(session, event)


async def run_lead_ad_capture(ctx: dict[str, object], payload_dict: dict[str, Any]) -> None:
    """Process a single Facebook Lead Ads leadgen event.

    Fetches actual field values from the Meta Graph API using the stored page access
    token, normalises the form submission, and runs the capture pipeline.
    Bypasses the two-stage LLM filter — form submissions are explicit lead intent.
    """
    tenant_id = UUID(cast(str, payload_dict["tenant_id"]))
    channel_connection_id = UUID(cast(str, payload_dict["channel_connection_id"]))
    leadgen_id = cast(str, payload_dict["leadgen_id"])
    raw_payload: dict[str, Any] = cast(dict[str, Any], payload_dict.get("raw_payload", {}))

    settings = get_settings()
    factory = cast(async_sessionmaker[AsyncSession], ctx["session_factory"])

    async with factory() as session:
        connection = await get_channel_connection(session, channel_connection_id)
        if connection is None or not connection.credentials_encrypted:
            return

        creds = decrypt_credentials(
            connection.credentials_encrypted,
            key=settings.channel_credentials_encryption_key,
        )
        page_access_token: str = cast(str, creds["page_access_token"])

        try:
            field_data = await fetch_lead_fields(leadgen_id, page_access_token, settings=settings)
        except ExternalServiceError:
            return

        event = normalise_lead_ad_form(
            field_data,
            leadgen_id=leadgen_id,
            tenant_id=tenant_id,
            channel_connection_id=channel_connection_id,
            raw_event_json=raw_payload or None,
        )
        await run_capture(session, event)


async def run_lead_capture_batch(ctx: dict[str, object], payload_dict: dict[str, Any]) -> None:
    """Process a batch of file-upload rows.

    Pre-flight is checked once; if the tenant has no active config the whole
    batch is aborted.  Per-row failures roll back that row's transaction and
    continue — "never discard" means failed rows are skipped, not lost.
    """
    tenant_id = UUID(cast(str, payload_dict["tenant_id"]))
    rows: list[dict[str, str]] = cast(list[dict[str, str]], payload_dict["rows"])

    factory = cast(async_sessionmaker[AsyncSession], ctx["session_factory"])
    async with factory() as session:
        try:
            await check_pre_flight(session, tenant_id)
        except PreFlightHaltError:
            return

        for i, row in enumerate(rows):
            try:
                event = normalise_file_row(row, tenant_id=tenant_id, row_index=i)
                if not event.phone and not event.email and not event.full_name:
                    lead = Lead(
                        tenant_id=tenant_id,
                        pipeline_stage="pre_flight_blocked",
                        source_channel=LeadSource.FILE_UPLOAD.value,
                        pre_flight_block_reason="insufficient_identity_fields",
                        raw_event_json=row,
                        extra_fields=event.extra_fields if event.extra_fields else None,
                    )
                    session.add(lead)
                    await session.commit()
                else:
                    await run_capture(session, event)
            except Exception as exc:
                await session.rollback()
                log.exception("batch_row_failed", row_index=i, error=str(exc))
