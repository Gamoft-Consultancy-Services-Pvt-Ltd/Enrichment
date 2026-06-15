"""Live Surepass GST-OTP test. Skipped unless SUREPASS_API_KEY is set.

This is the only test that hits the real Surepass API. It stays skipped until
credentials are available; run it once to confirm the provisional contract in
clients/surepass_client.py matches reality, then update that file if needed.
"""

import os

import pytest

from clients.surepass_client import send_gst_otp

pytestmark = pytest.mark.skipif(
    not os.getenv("SUREPASS_API_KEY"),
    reason="SUREPASS_API_KEY not set; live Surepass test skipped",
)

# A GSTIN you control, whose registered contact can receive the OTP.
_LIVE_GSTIN = os.getenv("SUREPASS_TEST_GSTIN", "")


async def test_live_send_gst_otp_returns_txn_ref() -> None:
    assert _LIVE_GSTIN, "set SUREPASS_TEST_GSTIN to run this test"
    # Requires surepass_use_mock=False in the environment.
    txn = await send_gst_otp(_LIVE_GSTIN)
    assert isinstance(txn, str) and txn
