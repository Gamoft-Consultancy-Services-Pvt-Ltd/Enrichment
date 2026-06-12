"""ARQ jobs for lead ingestion: file-upload batch and message-based webhook events."""

from typing import Any, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.config import get_settings
from modules.lead_ingestion.db.models import Lead
from modules.lead_ingestion.exceptions import PreFlightHaltError
from modules.lead_ingestion.normaliser import normalise_file_row, normalise_whatsapp_message
from modules.lead_ingestion.pipeline import run_capture, run_capture_message
from modules.lead_ingestion.pre_flight import check_pre_flight
from shared.events.schemas import LeadSource


async def run_lead_capture(
    ctx: dict[str, object], payload_dict: dict[str, Any]
) -> None:
    """Process a single message-based webhook event (WhatsApp, etc.)."""
    tenant_id = UUID(cast(str, payload_dict["tenant_id"]))
    channel_connection_id_str = cast(str | None, payload_dict.get("channel_connection_id"))
    channel_connection_id = UUID(channel_connection_id_str) if channel_connection_id_str else None
    raw_payload: dict[str, Any] = cast(dict[str, Any], payload_dict["raw_payload"])

    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        event = normalise_whatsapp_message(
            raw_payload,
            tenant_id=tenant_id,
            channel_connection_id=channel_connection_id,
        )
        await run_capture_message(session, event)

    await engine.dispose()


async def run_lead_capture_batch(
    ctx: dict[str, object], payload_dict: dict[str, Any]
) -> None:
    """Process a batch of file-upload rows.

    Pre-flight is checked once; if the tenant has no active config the whole
    batch is aborted.  Per-row failures roll back that row's transaction and
    continue — "never discard" means failed rows are skipped, not lost.
    """
    tenant_id = UUID(cast(str, payload_dict["tenant_id"]))
    rows: list[dict[str, str]] = cast(list[dict[str, str]], payload_dict["rows"])

    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        try:
            await check_pre_flight(session, tenant_id)
        except PreFlightHaltError:
            await engine.dispose()
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
            except Exception:
                await session.rollback()

    await engine.dispose()
