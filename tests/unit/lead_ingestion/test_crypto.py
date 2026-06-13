"""Unit tests for AES-256-GCM credential encryption — no DB, no network.

TDD: these tests are written BEFORE crypto.py exists.
Every test that touches encryption uses a freshly generated key so
there is no reliance on env state.
"""

import base64
import os

import pytest
from cryptography.exceptions import InvalidTag

from modules.lead_ingestion.crypto import decrypt_credentials, encrypt_credentials

# A valid 32-byte key encoded as URL-safe base64 (the format the app uses).
_KEY_BYTES = os.urandom(32)
_VALID_KEY = base64.urlsafe_b64encode(_KEY_BYTES).decode()


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_encrypt_decrypt_round_trip() -> None:
    """Encrypted blob → decrypt → same dict."""
    payload = {"access_token": "EAABsbCS", "phone_number_id": "1234567890"}
    blob = encrypt_credentials(payload, key=_VALID_KEY)
    recovered = decrypt_credentials(blob, key=_VALID_KEY)
    assert recovered == payload


def test_encrypt_produces_bytes() -> None:
    """encrypt_credentials returns bytes (suitable for LargeBinary column)."""
    blob = encrypt_credentials({"tok": "abc"}, key=_VALID_KEY)
    assert isinstance(blob, bytes)


def test_two_encryptions_of_same_payload_differ() -> None:
    """AES-GCM uses a random nonce so identical plaintexts produce different ciphertexts."""
    payload = {"access_token": "same-token"}
    blob1 = encrypt_credentials(payload, key=_VALID_KEY)
    blob2 = encrypt_credentials(payload, key=_VALID_KEY)
    assert blob1 != blob2


def test_decrypt_with_wrong_key_raises() -> None:
    """Decrypting with a different key raises InvalidTag (authentication tag mismatch)."""
    payload = {"access_token": "secret"}
    blob = encrypt_credentials(payload, key=_VALID_KEY)

    other_key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    with pytest.raises(InvalidTag):
        decrypt_credentials(blob, key=other_key)


def test_decrypt_tampered_ciphertext_raises() -> None:
    """Flipping a byte in the ciphertext invalidates the GCM auth tag."""
    payload = {"access_token": "secret"}
    blob = encrypt_credentials(payload, key=_VALID_KEY)
    # Flip one byte in the middle of the blob
    mutated = bytearray(blob)
    mutated[len(mutated) // 2] ^= 0xFF
    with pytest.raises(InvalidTag):
        decrypt_credentials(bytes(mutated), key=_VALID_KEY)


def test_encrypt_nested_dict_round_trips() -> None:
    """Nested dicts with various value types survive the round-trip."""
    payload = {
        "access_token": "EAABsbCS",
        "page_id": "123456",
        "expires_in": 5183944,
        "meta": {"page_name": "Acme Corp", "category": "Business"},
    }
    blob = encrypt_credentials(payload, key=_VALID_KEY)
    assert decrypt_credentials(blob, key=_VALID_KEY) == payload


def test_short_key_raises_value_error() -> None:
    """A key that decodes to fewer than 32 bytes must be rejected."""
    short_key = base64.urlsafe_b64encode(os.urandom(16)).decode()
    with pytest.raises(ValueError, match="32"):
        encrypt_credentials({"tok": "x"}, key=short_key)
