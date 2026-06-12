"""Unit tests for HMAC-SHA256 webhook signature validation — no DB, no network."""

import hashlib
import hmac

import pytest

from modules.lead_ingestion.exceptions import HmacValidationError
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
