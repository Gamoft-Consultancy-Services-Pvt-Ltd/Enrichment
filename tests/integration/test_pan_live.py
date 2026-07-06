"""Live PAN verification test. Skipped unless PAN_API_KEY is set.

The only test that hits the real Sandbox PAN API. Run it once credentials exist
(pan_use_mock=False, plus pan_api_key / pan_api_secret / pan_base_url in the
environment) to confirm the two-step contract in clients/pan_client.py.
"""

import os

import pytest

from clients.pan_client import verify_pan

pytestmark = pytest.mark.skipif(
    not os.getenv("PAN_API_KEY"),
    reason="PAN_API_KEY not set; live PAN test skipped",
)

_LIVE_PAN = os.getenv("PAN_TEST_PAN", "")
_LIVE_NAME = os.getenv("PAN_TEST_NAME", "")
_LIVE_DOB = os.getenv("PAN_TEST_DOB", "")  # DD/MM/YYYY


async def test_live_verify_pan_matches() -> None:
    assert _LIVE_PAN and _LIVE_NAME and _LIVE_DOB, "set PAN_TEST_PAN/NAME/DOB to run"
    # Requires pan_use_mock=False and the Sandbox secrets/base URL in the environment.
    result = await verify_pan(_LIVE_PAN, _LIVE_NAME, _LIVE_DOB)
    assert result["status"] == "valid"
    assert result["name_match"] is True
    assert result["dob_match"] is True
