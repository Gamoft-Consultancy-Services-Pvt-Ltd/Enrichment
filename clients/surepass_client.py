"""Thin async wrapper around Surepass GST-verification-with-OTP.

The only file in the project that talks to Surepass. Two calls:
`send_gst_otp` triggers an OTP to the GSTIN's GST-registered contact and returns
a transaction reference; `verify_gst_otp` submits that reference plus the OTP and
returns the verified company record (or None if the OTP was rejected).

Surepass's exact request/response contract is provisional pending API docs; when
the real contract is known, only the live branches below change. A config-gated
mock path (`surepass_use_mock`) lets the full onboarding flow run with no token.
"""

from typing import Any, TypedDict

import httpx

from core.config import get_settings
from core.exceptions import ExternalServiceError

_SEND_PATH = "/api/v1/corporate/gstin-otp"
_VERIFY_PATH = "/api/v1/corporate/gstin-otp-verify"
_DEV_OTP = "123456"


class CompanyData(TypedDict):
    """The verified GST record Surepass returns on a successful OTP."""

    gstin: str
    legal_name: str
    trade_name: str
    status: str
    address: str


def _mock_company(gstin: str) -> CompanyData:
    return CompanyData(
        gstin=gstin,
        legal_name="MOCK PRIVATE LIMITED",
        trade_name="Mock",
        status="Active",
        address="1 Mock Street, Test City",
    )


async def send_gst_otp(gstin: str) -> str:
    """Trigger a GST OTP and return the transaction reference.

    Raises ExternalServiceError on transport/API failure.
    """
    settings = get_settings()
    if settings.surepass_use_mock:
        return f"mock-txn-{gstin}"

    headers = {
        "Authorization": f"Bearer {settings.surepass_api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {"id_number": gstin}
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            response = await http.post(
                f"{settings.surepass_base_url}{_SEND_PATH}",
                headers=headers,
                json=payload,
            )
    except Exception as exc:
        raise ExternalServiceError(f"Surepass send_gst_otp failed: {exc}") from exc

    if response.status_code != 200:
        raise ExternalServiceError(f"Surepass send returned {response.status_code}")

    data: dict[str, Any] = response.json()
    client_id = data.get("data", {}).get("client_id")
    if not client_id:
        raise ExternalServiceError("Surepass send returned no client_id")
    return str(client_id)


async def verify_gst_otp(txn_ref: str, otp: str) -> CompanyData | None:
    """Submit the OTP. Return the verified company on success, None if rejected.

    Raises ExternalServiceError on transport failure (not on OTP rejection).
    """
    settings = get_settings()
    if settings.surepass_use_mock:
        if otp != _DEV_OTP:
            return None
        gstin = txn_ref.removeprefix("mock-txn-")
        return _mock_company(gstin)

    headers = {
        "Authorization": f"Bearer {settings.surepass_api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {"client_id": txn_ref, "otp": otp}
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            response = await http.post(
                f"{settings.surepass_base_url}{_VERIFY_PATH}",
                headers=headers,
                json=payload,
            )
    except Exception as exc:
        raise ExternalServiceError(f"Surepass verify_gst_otp failed: {exc}") from exc

    if response.status_code >= 500:
        # A server-side outage is NOT a wrong OTP; surfacing it as an error keeps the
        # caller from burning the tenant's attempts (and locking them out) on our dime.
        raise ExternalServiceError(f"Surepass verify returned {response.status_code}")
    if response.status_code != 200:
        # A 4xx means Surepass rejected the OTP (wrong/expired) — a domain outcome.
        return None

    record: dict[str, Any] = response.json().get("data", {})
    return CompanyData(
        gstin=str(record.get("gstin", "")),
        legal_name=str(record.get("legal_name", "")),
        trade_name=str(record.get("trade_name", "")),
        status=str(record.get("status", "")),
        address=str(record.get("address", "")),
    )
