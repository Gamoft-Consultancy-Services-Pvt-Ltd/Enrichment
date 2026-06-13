"""Source adapters: raw channel payloads → NormalisedChannelEvent.

Sprint 2: file-row adapter.
Sprint 3: WhatsApp DM adapter.
Sprint 4: Instagram DM adapter, Facebook DM adapter, Lead Ad adapter.
"""

import hashlib
import json
from typing import Any
from uuid import UUID

from modules.lead_ingestion.schemas.lead_form import STANDARD_FIELD_MAP
from modules.lead_ingestion.schemas.normalised_event import NormalisedChannelEvent
from shared.events.schemas import LeadSource


def normalise_whatsapp_message(
    payload: dict[str, Any],
    *,
    tenant_id: UUID,
    channel_connection_id: UUID | None = None,
) -> NormalisedChannelEvent:
    """Convert one WhatsApp webhook message object to NormalisedChannelEvent.

    Expects the full webhook payload (the outermost dict with "object" and
    "entry" keys).  Extracts the first message from the first entry.

    Meta webhook shape:
      payload["entry"][0]["changes"][0]["value"]["messages"][0]  → message
      payload["entry"][0]["changes"][0]["value"]["contacts"][0]  → sender profile
    """
    value: dict[str, Any] = payload["entry"][0]["changes"][0]["value"]
    message: dict[str, Any] = value["messages"][0]
    contacts: list[dict[str, Any]] = value.get("contacts", [])

    message_id: str = message["id"]  # e.g. "wamid.XXXX"
    # Meta sends the WA ID as a plain MSISDN (digits only, no +).
    # Prepend + so the value is E.164 and deduplicates against CSV/other-source leads.
    _raw_phone: str = message["from"]
    sender_phone: str = _raw_phone if _raw_phone.startswith("+") else f"+{_raw_phone}"

    full_name: str | None = None
    if contacts:
        full_name = contacts[0].get("profile", {}).get("name") or None

    raw_text: str | None = None
    if message.get("type") == "text":
        raw_text = message.get("text", {}).get("body") or None

    return NormalisedChannelEvent(
        tenant_id=tenant_id,
        channel_connection_id=channel_connection_id,
        source=LeadSource.WHATSAPP,
        platform_event_id=message_id,
        full_name=full_name,
        phone=sender_phone,
        raw_text=raw_text,
        raw_event_json=payload,
    )


def normalise_file_row(
    row: dict[str, Any],
    *,
    tenant_id: UUID,
    channel_connection_id: UUID | None = None,
    row_index: int = 0,
) -> NormalisedChannelEvent:
    """Convert one CSV/XLSX dict row to NormalisedChannelEvent.

    - Headers in STANDARD_FIELD_MAP → canonical identity fields.
    - Unrecognised non-empty headers → extra_fields (never dropped).
    - platform_event_id is a deterministic hash of tenant + row content:
      re-uploading the same file won't duplicate rows (idempotent).
    """
    canonical: dict[str, str] = {}
    extra: dict[str, Any] = {}

    for header, value in row.items():
        str_value = str(value).strip() if value is not None else ""
        if not str_value:
            continue
        target = STANDARD_FIELD_MAP.get(header)
        if target is not None and target not in canonical:
            canonical[target] = str_value
        elif target is None:
            extra[header] = str_value

    return NormalisedChannelEvent(
        tenant_id=tenant_id,
        channel_connection_id=channel_connection_id,
        source=LeadSource.FILE_UPLOAD,
        platform_event_id=_row_hash(tenant_id, row),
        full_name=canonical.get("full_name"),
        phone=canonical.get("phone"),
        email=canonical.get("email"),
        location=canonical.get("location"),
        raw_event_json={k: str(v) if v is not None else "" for k, v in row.items()},
        extra_fields=extra,
    )


def normalise_instagram_dm(
    payload: dict[str, Any],
    *,
    tenant_id: UUID,
    channel_connection_id: UUID | None = None,
) -> NormalisedChannelEvent:
    """Convert one Instagram DM webhook message object to NormalisedChannelEvent.

    Meta Messenger webhook shape for Instagram DMs:
      payload["entry"][0]["messaging"][0]  → messaging event
      messaging["sender"]["id"]            → IGSID (Instagram-scoped ID)
      messaging["message"]["mid"]          → message ID (platform_event_id)
      messaging["message"]["text"]         → message text (optional)

    Args:
        payload: Full Instagram DM webhook payload dict.
        tenant_id: The tenant this connection belongs to.
        channel_connection_id: Optional ChannelConnection FK.

    Returns:
        NormalisedChannelEvent with source=LeadSource.INSTAGRAM.
    """
    messaging: dict[str, Any] = payload["entry"][0]["messaging"][0]
    sender_igsid: str = messaging["sender"]["id"]
    message: dict[str, Any] = messaging.get("message", {})
    message_id: str = message.get("mid", f"ig-{sender_igsid}")
    raw_text: str | None = message.get("text") or None

    return NormalisedChannelEvent(
        tenant_id=tenant_id,
        channel_connection_id=channel_connection_id,
        source=LeadSource.INSTAGRAM,
        platform_event_id=message_id,
        phone=None,  # Instagram DMs do not expose phone numbers
        raw_text=raw_text,
        raw_event_json=payload,
    )


def normalise_facebook_dm(
    payload: dict[str, Any],
    *,
    tenant_id: UUID,
    channel_connection_id: UUID | None = None,
) -> NormalisedChannelEvent:
    """Convert one Facebook Messenger DM webhook message to NormalisedChannelEvent.

    Meta Messenger webhook shape for Facebook Page DMs:
      payload["entry"][0]["messaging"][0]  → messaging event
      messaging["sender"]["id"]            → PSID (Page-scoped ID)
      messaging["message"]["mid"]          → message ID (platform_event_id)
      messaging["message"]["text"]         → message text (optional)

    Args:
        payload: Full Facebook DM webhook payload dict.
        tenant_id: The tenant this connection belongs to.
        channel_connection_id: Optional ChannelConnection FK.

    Returns:
        NormalisedChannelEvent with source=LeadSource.FACEBOOK.
    """
    messaging: dict[str, Any] = payload["entry"][0]["messaging"][0]
    sender_psid: str = messaging["sender"]["id"]
    message: dict[str, Any] = messaging.get("message", {})
    message_id: str = message.get("mid", f"fb-{sender_psid}")
    raw_text: str | None = message.get("text") or None

    return NormalisedChannelEvent(
        tenant_id=tenant_id,
        channel_connection_id=channel_connection_id,
        source=LeadSource.FACEBOOK,
        platform_event_id=message_id,
        phone=None,  # Facebook DMs do not expose phone numbers in webhook
        raw_text=raw_text,
        raw_event_json=payload,
    )


# ---------------------------------------------------------------------------
# Lead Ad form field name → canonical identity field
# ---------------------------------------------------------------------------

_LEAD_AD_FIELD_MAP: dict[str, str] = {
    # name variants
    "full_name": "full_name",
    "name": "full_name",
    "first_name": "full_name",
    # phone variants
    "phone_number": "phone",
    "phone": "phone",
    "mobile": "phone",
    # email variants
    "email": "email",
    "email_address": "email",
    # location variants
    "city": "location",
    "location": "location",
    "area": "location",
}


def normalise_lead_ad(
    leadgen_id: str,
    form_data: dict[str, Any],
    *,
    tenant_id: UUID,
    channel_connection_id: UUID | None = None,
    source: LeadSource = LeadSource.FACEBOOK_LEAD_AD,
) -> NormalisedChannelEvent:
    """Convert Meta Lead Ad form data to NormalisedChannelEvent.

    The *form_data* is the response from the Graph API
    GET /{leadgen_id}?fields=field_data, which has the shape:
      {"id": "...", "field_data": [{"name": "full_name", "values": ["..."]}]}

    platform_event_id is set to "leadgen-{leadgen_id}" for idempotency.

    Args:
        leadgen_id: The leadgen ID from the webhook (used as idempotency key).
        form_data: The Graph API response dict containing 'field_data'.
        tenant_id: The tenant this connection belongs to.
        channel_connection_id: Optional ChannelConnection FK.
        source: LeadSource.FACEBOOK_LEAD_AD or INSTAGRAM_LEAD_AD.

    Returns:
        NormalisedChannelEvent with source set to *source*.
    """
    canonical: dict[str, str] = {}
    extra: dict[str, Any] = {}

    for field in form_data.get("field_data", []):
        field_name: str = field.get("name", "")
        values: list[str] = field.get("values", [])
        value = values[0] if values else ""
        if not value:
            continue
        target = _LEAD_AD_FIELD_MAP.get(field_name.lower())
        if target is not None and target not in canonical:
            canonical[target] = value
        elif target is None:
            extra[field_name] = value

    # Build a synthetic full_name from first_name if full_name wasn't mapped
    if "full_name" not in canonical and "first_name" in extra:
        canonical["full_name"] = extra.pop("first_name")

    return NormalisedChannelEvent(
        tenant_id=tenant_id,
        channel_connection_id=channel_connection_id,
        source=source,
        platform_event_id=f"leadgen-{leadgen_id}",
        full_name=canonical.get("full_name"),
        phone=canonical.get("phone"),
        email=canonical.get("email"),
        location=canonical.get("location"),
        raw_event_json=form_data,
        extra_fields=extra,
    )


def _row_hash(tenant_id: UUID, row: dict[str, Any]) -> str:
    payload = json.dumps(
        {"t": str(tenant_id), "r": {k: str(v) for k, v in row.items()}},
        sort_keys=True,
    )
    return "file-" + hashlib.sha256(payload.encode()).hexdigest()[:40]
