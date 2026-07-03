"""COMP-301 — data minimization at the file-upload intake boundary.

ST1 + ST2: field classification model and required attribute list.
ST3 + ST4: collection justification per field.
ST5: sensitive data detection and stripping for extra_fields.
"""

import re
from enum import StrEnum
from typing import Any


class FieldSensitivity(StrEnum):
    PII_DIRECT = "PII_DIRECT"  # full_name, phone, email
    PII_INDIRECT = "PII_INDIRECT"  # location
    PII_POSSIBLE = "PII_POSSIBLE"  # raw_event_json, extra_fields
    OPERATIONAL = "OPERATIONAL"  # pipeline_stage, source_channel, id, created_at


LEAD_FIELD_CLASSIFICATIONS: dict[str, FieldSensitivity] = {
    "full_name": FieldSensitivity.PII_DIRECT,
    "phone": FieldSensitivity.PII_DIRECT,
    "email": FieldSensitivity.PII_DIRECT,
    "location": FieldSensitivity.PII_INDIRECT,
    "raw_event_json": FieldSensitivity.PII_POSSIBLE,
    "extra_fields": FieldSensitivity.PII_POSSIBLE,
    "pipeline_stage": FieldSensitivity.OPERATIONAL,
    "source_channel": FieldSensitivity.OPERATIONAL,
}

# ST4 — justification for every collected field
COLLECTION_JUSTIFICATION: dict[str, str] = {
    "full_name": "Lead identity matching and tenant CRM display",
    "phone": "Primary dedup key (E.164); tenant outreach channel",
    "email": "Secondary dedup key; tenant outreach channel",
    "location": "Geographic ICP scoring signal",
    "raw_event_json": "Source-of-truth audit trail; required for idempotent redelivery",
    "extra_fields": "Tenant-defined fields; stored after sensitive field stripping",
}

# ---------------------------------------------------------------------------
# ST3 — regex patterns (Indian regulatory context)
# ---------------------------------------------------------------------------
_AADHAAR = re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b")
_PAN = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
# Indian IFSC: 4 letters + literal '0' + 6 alphanumeric
_IFSC = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")
# Indian passport: 1 uppercase letter + 7 digits (8 chars total)
_PASSPORT = re.compile(r"\b[A-Z][0-9]{7}\b")
# Credit card: 13-19 digits optionally separated by spaces or hyphens; Luhn check applied below.
# Pattern: start with one digit, then 12-18 more (optional separator + digit).
# Aadhaar is exactly 12 digits — below the 13-digit CC minimum — so no regex overlap.
_CC_RAW = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")


def _luhn_valid(digits: str) -> bool:
    """Return True if the digit string passes the Luhn check."""
    nums = [int(d) for d in digits]
    nums.reverse()
    total = 0
    for i, d in enumerate(nums):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _classify_value(raw: str) -> str | None:
    """Return detection label if raw contains a regulated identifier, else None.

    Values are uppercased before matching to catch lowercase PAN/IFSC/Passport.
    Aadhaar pattern is digits-only so case is irrelevant.
    """
    upper = raw.upper()
    # Check CC first: its 13-19 digit minimum means it cannot overlap with 12-digit Aadhaar.
    for match in _CC_RAW.finditer(upper):
        digits_only = re.sub(r"[^0-9]", "", match.group())
        if 13 <= len(digits_only) <= 19 and _luhn_valid(digits_only):
            return "CREDIT_CARD"
    if _AADHAAR.search(upper):
        return "AADHAAR"
    if _PAN.search(upper):
        return "PAN"
    if _IFSC.search(upper):
        return "IFSC"
    if _PASSPORT.search(upper):
        return "PASSPORT"
    return None


def detect_sensitive_fields(data: dict[str, Any]) -> dict[str, str]:
    """Return {field_name: detection_type} for any values that contain regulated identifiers.

    Values are NOT logged — only key names are returned.
    """
    result: dict[str, str] = {}
    for key, val in data.items():
        s = str(val).strip() if val is not None else ""
        if not s:
            continue
        detection = _classify_value(s)
        if detection is not None:
            result[key] = detection
    return result


def strip_sensitive_fields(
    extra_fields: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Return (clean_dict, {removed_key: detection_type}).

    Builds a new dict; never mutates the input.
    The removed values are NEVER stored or logged — only the key names.
    """
    detections = detect_sensitive_fields(extra_fields)
    clean = {k: v for k, v in extra_fields.items() if k not in detections}
    return clean, detections
