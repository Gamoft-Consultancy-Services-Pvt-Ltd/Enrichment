"""Helpers for minting RS256 JWTs and JWKS in auth tests (no network)."""

from __future__ import annotations

import time
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk, jwt
from jose.constants import ALGORITHMS


def make_keypair(kid: str = "test-key") -> tuple[str, dict[str, Any]]:
    """Return (private_pem, public_jwk) where the JWK carries the given kid."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    jwk_dict = jwk.construct(public_pem, ALGORITHMS.RS256).to_dict()
    jwk_dict = {k: (v.decode() if isinstance(v, bytes) else v) for k, v in jwk_dict.items()}
    jwk_dict.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return private_pem, jwk_dict


def make_token(
    private_pem: str,
    *,
    kid: str,
    audience: str,
    issuer: str,
    claims: dict[str, Any],
    expires_in: int = 3600,
) -> str:
    """Sign a JWT with the given private key, kid, aud/iss, and extra claims."""
    now = int(time.time())
    payload = {**claims, "iss": issuer, "aud": audience, "iat": now, "exp": now + expires_in}
    encoded: str = jwt.encode(payload, private_pem, algorithm="RS256", headers={"kid": kid})
    return encoded
