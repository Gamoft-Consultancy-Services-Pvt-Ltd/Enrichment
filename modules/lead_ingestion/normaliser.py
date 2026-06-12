"""Source adapters: raw channel payloads → NormalisedChannelEvent.

Sprint 2: file-row adapter only.  WhatsApp / Facebook / email / sheets
adapters are added in Sprints 3-5.
"""

import hashlib
import json
from typing import Any
from uuid import UUID

from modules.lead_ingestion.schemas.lead_form import STANDARD_FIELD_MAP
from modules.lead_ingestion.schemas.normalised_event import NormalisedChannelEvent
from shared.events.schemas import LeadSource


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


def _row_hash(tenant_id: UUID, row: dict[str, Any]) -> str:
    payload = json.dumps(
        {"t": str(tenant_id), "r": {k: str(v) for k, v in row.items()}},
        sort_keys=True,
    )
    return "file-" + hashlib.sha256(payload.encode()).hexdigest()[:40]
