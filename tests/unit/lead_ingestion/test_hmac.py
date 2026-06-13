"""Unit tests for HMAC-SHA256 webhook signature validation and OAuth state signing.

No DB, no network.
"""

import hashlib
import hmac

import pytest

from modules.lead_ingestion.exceptions import HmacValidationError, OAuthStateError
from modules.lead_ingestion.oauth.state import sign_state, verify_state
from modules.lead_ingestion.webhook_receiver import validate_signature

_SECRET = "test_app_secret"


def _make_signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_valid_signature_passes() -> None:
    body = b'{"object": "whatsapp_business_account"}'
    sig = _make_signature(body, _SECRET)
    validate_signature(body, sig, app_secret=_SECRET)  # must not raise


def test_tampered_body_raises() -> None:
    body = b'{"object": "whatsapp_business_account"}'
    sig = _make_signature(body, _SECRET)
    tampered = body[:-1] + b"!"
    with pytest.raises(HmacValidationError):
        validate_signature(tampered, sig, app_secret=_SECRET)


def test_wrong_secret_raises() -> None:
    body = b'{"object": "whatsapp_business_account"}'
    sig = _make_signature(body, "wrong_secret")
    with pytest.raises(HmacValidationError):
        validate_signature(body, sig, app_secret=_SECRET)


def test_malformed_header_raises() -> None:
    body = b'{"object": "whatsapp_business_account"}'
    with pytest.raises(HmacValidationError):
        validate_signature(body, "not-a-valid-sig", app_secret=_SECRET)


def test_missing_sha256_prefix_raises() -> None:
    body = b'{"object": "whatsapp_business_account"}'
    digest = hmac.new(_SECRET.encode(), body, hashlib.sha256).hexdigest()
    with pytest.raises(HmacValidationError):
        validate_signature(body, digest, app_secret=_SECRET)  # no "sha256=" prefix


# ---------------------------------------------------------------------------
# OAuth state sign / verify — Sprint 4
# ---------------------------------------------------------------------------

_STATE_SECRET = "oauth_state_signing_secret_32bytes!"


def test_oauth_state_sign_verify_round_trip() -> None:
    """sign_state → verify_state must return the original payload unchanged."""
    payload = {"tenant_id": "abc-123", "channel": "whatsapp", "nonce": "xyz"}
    token = sign_state(payload, secret=_STATE_SECRET)
    recovered = verify_state(token, secret=_STATE_SECRET)
    assert recovered == payload


def test_oauth_state_token_is_str() -> None:
    """sign_state must return a str (URL-safe for redirect_uri query params)."""
    token = sign_state({"x": "1"}, secret=_STATE_SECRET)
    assert isinstance(token, str)


def test_oauth_state_forged_token_raises_403_error() -> None:
    """A state token signed with a different secret must raise OAuthStateError."""
    token = sign_state({"tenant_id": "t1"}, secret="wrong_secret")
    with pytest.raises(OAuthStateError):
        verify_state(token, secret=_STATE_SECRET)


def test_oauth_state_tampered_token_raises() -> None:
    """Flipping a character in the HMAC portion must raise OAuthStateError."""
    token = sign_state({"tenant_id": "t1"}, secret=_STATE_SECRET)
    # The token format is <b64-payload>.<hmac>; tamper with the last character.
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(OAuthStateError):
        verify_state(tampered, secret=_STATE_SECRET)


def test_oauth_state_malformed_token_raises() -> None:
    """A token with no '.' separator must raise OAuthStateError."""
    with pytest.raises(OAuthStateError):
        verify_state("not-a-valid-state-token", secret=_STATE_SECRET)


def test_oauth_state_different_payloads_produce_different_tokens() -> None:
    """Different payloads must produce different signed tokens."""
    t1 = sign_state({"tenant_id": "t1"}, secret=_STATE_SECRET)
    t2 = sign_state({"tenant_id": "t2"}, secret=_STATE_SECRET)
    assert t1 != t2
