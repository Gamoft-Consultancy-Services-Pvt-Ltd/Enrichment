"""Public surface of the lead_ingestion module.

api/ and workers/ must import exclusively from this file and schemas.py.
Never import internal files (pipeline, normaliser, db/, etc.) from outside this module.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from modules.lead_ingestion.crypto import decrypt_credentials
from modules.lead_ingestion.db import repository
from modules.lead_ingestion.db.models import Lead
from modules.lead_ingestion.erasure import erase_lead_by_identity
from modules.lead_ingestion.exceptions import (
    ChannelApiError,
    DuplicateEventError,
    FilterClientError,
    HmacValidationError,
    OAuthStateError,
    PreFlightHaltError,
)
from modules.lead_ingestion.file_upload_handler import handle_file_upload
from modules.lead_ingestion.normaliser import (
    normalise_facebook_dm,
    normalise_file_row,
    normalise_instagram_dm,
    normalise_lead_ad_form,
    normalise_whatsapp_message,
)
from modules.lead_ingestion.oauth.facebook import build_facebook_auth_url, exchange_facebook_code
from modules.lead_ingestion.oauth.instagram import (
    build_instagram_auth_url,
    exchange_instagram_code,
)
from modules.lead_ingestion.oauth.whatsapp import exchange_whatsapp_signup_code
from modules.lead_ingestion.pipeline import run_capture, run_capture_message
from modules.lead_ingestion.pre_flight import check_pre_flight
from modules.lead_ingestion.webhook_receiver import validate_signature
from shared.events.schemas import EnrichmentResult

get_whatsapp_connection_by_phone_number_id = repository.get_whatsapp_connection_by_phone_number_id
get_connection_by_page_or_ig_account_id = repository.get_connection_by_page_or_ig_account_id
get_channel_connection = repository.get_channel_connection
log_unroutable_event = repository.log_unroutable_event
log_failed_intake_event = repository.log_failed_intake_event
get_lead_by_id = repository.get_lead_by_id


async def store_lead_enrichment(
    session: AsyncSession, lead_id: UUID, result: EnrichmentResult
) -> Lead:
    """Persist an EnrichmentResult onto the lead row (leads.enrichment + enriched_at).

    Raises ValueError if the lead does not exist.
    """
    lead = await repository.set_lead_enrichment(session, lead_id, result.model_dump(mode="json"))
    if lead is None:
        raise ValueError(f"lead {lead_id} not found")
    return lead


__all__ = [
    # Exceptions
    "ChannelApiError",
    "DuplicateEventError",
    "FilterClientError",
    "HmacValidationError",
    "OAuthStateError",
    "PreFlightHaltError",
    # ORM model (re-exported for workers that construct Lead rows directly)
    "Lead",
    # Crypto (used by workers to decrypt ChannelConnection credentials)
    "decrypt_credentials",
    # File upload
    "handle_file_upload",
    # Normalisers
    "normalise_facebook_dm",
    "normalise_file_row",
    "normalise_instagram_dm",
    "normalise_lead_ad_form",
    "normalise_whatsapp_message",
    # Pipeline
    "run_capture",
    "run_capture_message",
    "check_pre_flight",
    # Webhook
    "validate_signature",
    "get_whatsapp_connection_by_phone_number_id",
    "get_connection_by_page_or_ig_account_id",
    "get_channel_connection",
    "log_unroutable_event",
    "log_failed_intake_event",
    "get_lead_by_id",
    # Enrichment persistence (store_lead_enrichment writes EnrichmentResult onto the lead)
    "store_lead_enrichment",
    # OAuth — Facebook
    "build_facebook_auth_url",
    "exchange_facebook_code",
    # OAuth — Instagram
    "build_instagram_auth_url",
    "exchange_instagram_code",
    # OAuth — WhatsApp Embedded Signup
    "exchange_whatsapp_signup_code",
    # GDPR erasure
    "erase_lead_by_identity",
]
