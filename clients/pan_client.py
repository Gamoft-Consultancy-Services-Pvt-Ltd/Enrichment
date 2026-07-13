"""Thin async wrapper around Sandbox's (Quicko) PAN verification API.

Mock-first: when settings.pan_use_mock is True (the default) no network call is
made, so onboarding runs with no provider credentials. The live path follows
Sandbox's two-step contract — POST /authenticate to mint a 24h JWT, then
POST /kyc/pan/verify. tests/integration/test_pan_live.py confirms it end-to-end
once credentials exist. This client only transports + parses; the verification
gate (status valid AND name/DOB match) lives in modules/tenant_onboarding/kyb.py.

Failure policy: this client runs synchronously inside POST /onboarding, and the
app_error_handler echoes ExternalServiceError.message straight to the HTTP caller.
So every failure (network, non-200, or an unexpected/unparseable body) raises
ExternalServiceError with a fixed GENERIC message; the real cause (status code,
exception, key) is logged, never returned to the caller and never includes the
api_key/api_secret. Provider failure is surfaced — never silently treated as
"not verified".
"""

from typing import Any, TypedDict

import httpx

from core.config import get_settings
from core.exceptions import ExternalServiceError
from core.logging import get_logger

logger = get_logger(__name__)

# A valid-format PAN reserved by the mock to simulate a failed verification.
_MOCK_NOT_FOUND_PAN = "AAAAA0000A"
_API_VERSION = "1.0.0"
# Single client-facing message — no provider internals reach the HTTP caller.
_GENERIC_ERROR = "PAN verification is temporarily unavailable; please try again"

# 4th character of a PAN encodes the holder type.
_CATEGORY_BY_TYPE_CHAR = {
    "P": "individual",
    "C": "company",
    "H": "huf",
    "F": "firm",
    "T": "trust",
}


class PanCheck(TypedDict):
    """Parsed Sandbox response: holder category, validity status, and match flags."""

    category: str
    status: str
    name_match: bool
    dob_match: bool


async def verify_pan(pan: str, name: str, dob: str) -> PanCheck:
    """Verify a PAN against Sandbox (or the mock). Returns the parsed check.
    Raise ExternalServiceError (generic message) on auth/transport/provider failure."""
    settings = get_settings()
    if settings.pan_use_mock:
        return _mock_verify(pan)
    # Billed production endpoint in prod/staging; free test endpoint in dev (see
    # Settings.pan_effective_base_url) so development onboarding runs cost nothing.
    # Credentials must match the host: live pair for prod, test pair for dev/test.
    return await _live_verify(
        pan,
        name,
        dob,
        settings.pan_effective_api_key,
        settings.pan_effective_api_secret,
        settings.pan_effective_base_url,
    )


def _mock_verify(pan: str) -> PanCheck:
    if pan == _MOCK_NOT_FOUND_PAN or len(pan) < 4:
        return PanCheck(category="", status="invalid", name_match=False, dob_match=False)
    category = _CATEGORY_BY_TYPE_CHAR.get(pan[3], "other")
    return PanCheck(category=category, status="valid", name_match=True, dob_match=True)


async def _authenticate(api_key: str, api_secret: str, base_url: str) -> str:
    headers = {
        "x-api-key": api_key,
        "x-api-secret": api_secret,
        "x-api-version": _API_VERSION,
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            response = await http.post(f"{base_url}/authenticate", headers=headers)
    except httpx.HTTPError as exc:
        logger.error("pan_authenticate_network_error", error=str(exc))
        raise ExternalServiceError(_GENERIC_ERROR) from exc
    if response.status_code != 200:
        logger.error("pan_authenticate_bad_status", status=response.status_code)
        raise ExternalServiceError(_GENERIC_ERROR)
    try:
        token: str = response.json()["access_token"]
    except (ValueError, KeyError, TypeError) as exc:
        logger.error("pan_authenticate_bad_body", error=str(exc))
        raise ExternalServiceError(_GENERIC_ERROR) from exc
    return token


async def _live_verify(
    pan: str, name: str, dob: str, api_key: str, api_secret: str, base_url: str
) -> PanCheck:
    token = await _authenticate(api_key, api_secret, base_url)
    # Confirmed against Sandbox's verify curl: Authorization + Content-Type + x-api-key
    # only (no x-api-version on this call), token passed raw (no "Bearer" prefix).
    headers = {
        "Authorization": token,
        "x-api-key": api_key,
        "Content-Type": "application/json",
    }
    body = {
        "@entity": "in.co.sandbox.kyc.pan_verification.request",
        "pan": pan,
        "name_as_per_pan": name,
        "date_of_birth": dob,
        "consent": "Y",
        "reason": "Tenant onboarding KYB verification for the lead intelligence platform",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            response = await http.post(f"{base_url}/kyc/pan/verify", headers=headers, json=body)
    except httpx.HTTPError as exc:
        logger.error("pan_verify_network_error", error=str(exc))
        raise ExternalServiceError(_GENERIC_ERROR) from exc

    # Any non-200 (auth/input/503 Source Unavailable/provider down) is surfaced, never
    # silently treated as "not verified".
    if response.status_code != 200:
        logger.error("pan_verify_bad_status", status=response.status_code)
        raise ExternalServiceError(_GENERIC_ERROR)

    # Parse defensively: an unexpected body shape becomes a clean 502, not a raw 500.
    try:
        data: dict[str, Any] = response.json()["data"]
        return PanCheck(
            category=data.get("category", ""),
            status=data.get("status", ""),
            name_match=bool(data.get("name_as_per_pan_match")),
            dob_match=bool(data.get("date_of_birth_match")),
        )
    except (ValueError, KeyError, TypeError) as exc:
        logger.error("pan_verify_bad_body", error=str(exc))
        raise ExternalServiceError(_GENERIC_ERROR) from exc
