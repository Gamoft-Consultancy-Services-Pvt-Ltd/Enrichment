"""HMAC-signed OAuth state parameter helpers.

sign_state(payload, *, secret) -> str
    Serialises *payload* to JSON, base64url-encodes it, appends an HMAC-SHA256
    signature, and returns "<b64payload>.<hmac>" as a URL-safe string.

verify_state(token, *, secret) -> dict[str, Any]
    Verifies the HMAC (constant-time), decodes the payload, and returns it.
    Raises OAuthStateError on any failure.

Security note: HMAC comparison uses hmac.compare_digest (constant-time) to
prevent timing oracle attacks — never replace with ==.
"""

import base64
import hashlib
import hmac
import json
from typing import Any

from modules.lead_ingestion.exceptions import OAuthStateError


def _sign(b64_payload: str, *, secret: str) -> str:
    """Return the hex HMAC-SHA256 of *b64_payload* under *secret*."""
    return hmac.new(secret.encode(), b64_payload.encode(), hashlib.sha256).hexdigest()


def sign_state(payload: dict[str, Any], *, secret: str) -> str:
    """Create a signed state token for an OAuth redirect.

    Args:
        payload: Arbitrary JSON-serialisable dict embedded in the state param.
        secret: HMAC signing secret (e.g. settings.meta_app_secret).

    Returns:
        "<base64url-payload>.<hex-hmac>" — safe for use in URLs.
    """
    serialised = json.dumps(payload, sort_keys=True).encode()
    b64_payload = base64.urlsafe_b64encode(serialised).decode().rstrip("=")
    signature = _sign(b64_payload, secret=secret)
    return f"{b64_payload}.{signature}"


def verify_state(token: str, *, secret: str) -> dict[str, Any]:
    """Verify and decode a state token produced by sign_state.

    Args:
        token: The state string received on the OAuth callback.
        secret: The same HMAC signing secret used when the token was created.

    Returns:
        The original payload dict.

    Raises:
        OAuthStateError: if the token is malformed, has no '.' separator,
                         or the HMAC does not match.
    """
    parts = token.split(".", 1)
    if len(parts) != 2:
        raise OAuthStateError("OAuth state token is malformed: missing '.' separator")

    b64_payload, received_sig = parts

    expected_sig = _sign(b64_payload, secret=secret)
    if not hmac.compare_digest(expected_sig, received_sig):
        raise OAuthStateError("OAuth state HMAC verification failed")

    try:
        # Re-add stripped padding before decoding
        padding = 4 - len(b64_payload) % 4
        padded = b64_payload + ("=" * (padding % 4))
        payload_bytes = base64.urlsafe_b64decode(padded)
        result: dict[str, Any] = json.loads(payload_bytes.decode())
    except Exception as exc:
        raise OAuthStateError(f"OAuth state payload could not be decoded: {exc}") from exc

    return result
