"""KYB surface for tenant onboarding: verify a PAN via the PAN client and apply
the Option-A gate (PAN valid AND name match AND DOB match).

The API calls this (not clients/ directly) so the dependency rule holds:
api -> modules -> clients. Returns the record to persist on success, or None.
"""

from typing import Any

from clients import pan_client


async def verify_pan_kyb(pan: str, name: str, dob: str) -> dict[str, Any] | None:
    """Verify a PAN with name + DOB. Return the company record to persist when the
    PAN is valid and both the name and DOB match; otherwise return None."""
    check = await pan_client.verify_pan(pan, name, dob)
    if check["status"] == "valid" and check["name_match"] and check["dob_match"]:
        return {"name": name, "category": check["category"], "status": check["status"]}
    return None
