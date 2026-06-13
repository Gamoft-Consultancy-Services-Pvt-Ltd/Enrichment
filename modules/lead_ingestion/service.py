"""Public surface of the lead_ingestion module.

api/ and workers/ must import exclusively from this file and schemas.py.
Never import internal files (pipeline, normaliser, db/, etc.) from outside this module.
"""

from modules.lead_ingestion.db import repository
from modules.lead_ingestion.db.models import Lead
from modules.lead_ingestion.exceptions import (
    ChannelApiError,
    DuplicateEventError,
    FilterClientError,
    HmacValidationError,
    OAuthStateError,
    PreFlightHaltError,
)
from modules.lead_ingestion.file_upload_handler import handle_file_upload
from modules.lead_ingestion.lead_retrieval_worker import process_lead_ad_webhook
from modules.lead_ingestion.normaliser import normalise_file_row, normalise_whatsapp_message
from modules.lead_ingestion.pipeline import run_capture, run_capture_message
from modules.lead_ingestion.pre_flight import check_pre_flight
from modules.lead_ingestion.webhook_receiver import validate_signature

get_whatsapp_connection_by_phone_number_id = repository.get_whatsapp_connection_by_phone_number_id

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
    # File upload
    "handle_file_upload",
    # Lead Ad
    "process_lead_ad_webhook",
    # Normalisers
    "normalise_file_row",
    "normalise_whatsapp_message",
    # Pipeline
    "run_capture",
    "run_capture_message",
    "check_pre_flight",
    # Webhook
    "validate_signature",
    "get_whatsapp_connection_by_phone_number_id",
]
