"""Meta webhook receiver: HMAC-SHA256 validation and payload routing.

validate_signature is a pure function — it takes app_secret as a parameter
so the API layer supplies it from settings and unit tests need no env/mocks.
"""

import hashlib
import hmac

from modules.lead_ingestion.exceptions import HmacValidationError


def validate_signature(
    raw_body: bytes,
    signature_header: str,
    *,
    app_secret: str,
) -> None:
    """Verify that signature_header matches HMAC-SHA256(raw_body, app_secret).

    Raises HmacValidationError on mismatch, missing prefix, or malformed header.
    Comparison is constant-time (hmac.compare_digest) to prevent timing attacks.
    """
    if not signature_header.startswith("sha256="):
        raise HmacValidationError("Missing sha256= prefix in X-Hub-Signature-256 header")

    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    received = signature_header.removeprefix("sha256=")

    if not hmac.compare_digest(expected, received):
        raise HmacValidationError("HMAC-SHA256 signature mismatch")
