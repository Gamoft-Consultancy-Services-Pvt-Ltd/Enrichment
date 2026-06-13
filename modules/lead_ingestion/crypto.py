"""AES-256-GCM credential encryption for ChannelConnection.credentials_encrypted.

Public surface:
  encrypt_credentials(payload, *, key) -> bytes
  decrypt_credentials(blob, *, key) -> dict[str, Any]

The key is a URL-safe base64-encoded 32-byte value stored in
settings.channel_credentials_encryption_key (env var).

Wire format: nonce (12 bytes) || ciphertext || tag (16 bytes)
(The cryptography library appends the tag to the ciphertext, so the stored
blob is: nonce (12) + ciphertext_with_tag.)
"""

import base64
import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_SIZE = 12  # 96-bit nonce — GCM standard
_KEY_SIZE = 32  # AES-256 requires a 32-byte key


def _decode_key(key: str) -> bytes:
    """Decode a URL-safe base64 key string and validate its length."""
    raw = base64.urlsafe_b64decode(key + "==")  # pad to avoid incorrect padding errors
    if len(raw) != _KEY_SIZE:
        raise ValueError(f"Encryption key must decode to exactly {_KEY_SIZE} bytes, got {len(raw)}")
    return raw


def encrypt_credentials(payload: dict[str, Any], *, key: str) -> bytes:
    """Encrypt *payload* dict to an opaque bytes blob using AES-256-GCM.

    Args:
        payload: Arbitrary JSON-serialisable dict (access tokens, IDs, etc.).
        key: URL-safe base64-encoded 32-byte encryption key.

    Returns:
        Encrypted bytes: nonce (12 bytes) + ciphertext+tag (variable length).

    Raises:
        ValueError: if the decoded key is not exactly 32 bytes.
    """
    raw_key = _decode_key(key)
    nonce = os.urandom(_NONCE_SIZE)
    aesgcm = AESGCM(raw_key)
    plaintext = json.dumps(payload, sort_keys=True).encode()
    ciphertext_with_tag = aesgcm.encrypt(nonce, plaintext, associated_data=None)
    return nonce + ciphertext_with_tag


def decrypt_credentials(blob: bytes, *, key: str) -> dict[str, Any]:
    """Decrypt a blob produced by encrypt_credentials.

    Args:
        blob: Bytes in the format: nonce (12) + ciphertext+tag.
        key: URL-safe base64-encoded 32-byte encryption key.

    Returns:
        The original payload dict.

    Raises:
        ValueError: if the decoded key is not exactly 32 bytes.
        cryptography.exceptions.InvalidTag: if authentication fails (wrong key
            or tampered ciphertext).
    """
    raw_key = _decode_key(key)
    nonce = blob[:_NONCE_SIZE]
    ciphertext_with_tag = blob[_NONCE_SIZE:]
    aesgcm = AESGCM(raw_key)
    plaintext = aesgcm.decrypt(nonce, ciphertext_with_tag, associated_data=None)
    result: dict[str, Any] = json.loads(plaintext.decode())
    return result
