"""Source adapters: raw channel payloads → NormalisedChannelEvent.

Sprint 2: file-row adapter.
Sprint 3: WhatsApp DM adapter.
Sprint 4: Instagram DM adapter, Facebook DM adapter.
Sprint 5: Facebook Lead Ads form adapter.
"""

import hashlib
import json
from typing import Any
from uuid import UUID

from modules.lead_ingestion.schemas.lead_form import STANDARD_FIELD_MAP
from modules.lead_ingestion.schemas.normalised_event import NormalisedChannelEvent
from shared.events.schemas import LeadSource

# Case-insensitive version of STANDARD_FIELD_MAP for CSV/XLSX header matching.
_FIELD_MAP_LOWER: dict[str, str] = {k.lower(): v for k, v in STANDARD_FIELD_MAP.items()}


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
        target = _FIELD_MAP_LOWER.get(header.strip().lower())
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


def normalise_lead_ad_form(
    field_data: list[dict[str, Any]],
    *,
    leadgen_id: str,
    tenant_id: UUID,
    channel_connection_id: UUID | None = None,
    raw_event_json: dict[str, Any] | None = None,
) -> NormalisedChannelEvent:
    """Convert Meta Lead Ads field_data to NormalisedChannelEvent.

    field_data is the list returned by GET /{leadgen_id}?fields=field_data.
    Each item is {"name": "<field>", "values": ["<value>"]}.
    Bypasses the two-stage LLM filter — caller must use run_capture directly.
    """
    flat: dict[str, str] = {}
    for item in field_data:
        name = str(item.get("name", "")).lower()
        values: list[Any] = item.get("values", [])
        value = str(values[0]).strip() if values else ""
        if value:
            flat[name] = value

    full_name: str | None = flat.get("full_name")
    if full_name is None:
        first = flat.get("first_name", "")
        last = flat.get("last_name", "")
        combined = f"{first} {last}".strip()
        full_name = combined or None

    email_raw = flat.get("email")
    email: str | None = email_raw.lower() if email_raw else None
    phone: str | None = flat.get("phone_number") or flat.get("work_phone_number")

    _location_fields = {"city", "state", "zip_code", "postal_code", "country"}
    location_parts = [flat[f] for f in _location_fields if f in flat]
    location: str | None = ", ".join(location_parts) or None

    _consumed = (
        {"full_name", "first_name", "last_name", "email", "phone_number", "work_phone_number"}
        | _location_fields
    )
    extra: dict[str, Any] = {k: v for k, v in flat.items() if k not in _consumed}

    return NormalisedChannelEvent(
        tenant_id=tenant_id,
        channel_connection_id=channel_connection_id,
        source=LeadSource.FACEBOOK_LEAD_ADS,
        platform_event_id=f"leadgen-{leadgen_id}",
        full_name=full_name,
        phone=phone,
        email=email,
        location=location,
        raw_event_json=raw_event_json or {"leadgen_id": leadgen_id, "field_data": field_data},
        extra_fields=extra,
    )


def _row_hash(tenant_id: UUID, row: dict[str, Any]) -> str:
    payload = json.dumps(
        {"t": str(tenant_id), "r": {k: str(v) for k, v in row.items()}},
        sort_keys=True,
    )
    return "file-" + hashlib.sha256(payload.encode()).hexdigest()[:40]
