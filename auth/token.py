"""Auth0 JWT verification: fetch & cache the JWKS, then verify the bearer token."""

from __future__ import annotations

from typing import Any

import httpx
from cachetools import TTLCache
from jose import jwt
from jose.exceptions import JWTError

from core.config import Settings, get_settings
from core.exceptions import AuthenticationError

_JWKS_TTL_SECONDS = 3600
_JWKS_CACHE: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=1, ttl=_JWKS_TTL_SECONDS)
_CACHE_KEY = "jwks"


def _issuer(settings: Settings) -> str:
    return f"https://{settings.auth0_domain}/"


def _jwks_url(settings: Settings) -> str:
    return f"https://{settings.auth0_domain}/.well-known/jwks.json"


def _get_jwks(*, force_refresh: bool = False) -> dict[str, Any]:
    """Return Auth0's JWKS, served from a 1-hour TTL cache; refetch when forced."""
    if not force_refresh and _CACHE_KEY in _JWKS_CACHE:
        return _JWKS_CACHE[_CACHE_KEY]
    try:
        response = httpx.get(_jwks_url(get_settings()), timeout=5.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise AuthenticationError("Unable to fetch signing keys") from exc
    jwks: dict[str, Any] = response.json()
    _JWKS_CACHE[_CACHE_KEY] = jwks
    return jwks


def _has_kid(jwks: dict[str, Any], kid: str | None) -> bool:
    return any(key.get("kid") == kid for key in jwks.get("keys", []))


def verify_token(token: str) -> dict[str, Any]:
    """Verify an Auth0 JWT (signature + issuer/audience/expiry); return its claims."""
    settings = get_settings()
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except JWTError as exc:
        raise AuthenticationError("Malformed token") from exc

    jwks = _get_jwks()
    if not _has_kid(jwks, kid):
        # Invalidation path: a rotated key we haven't cached — refetch once.
        jwks = _get_jwks(force_refresh=True)

    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            jwks,
            algorithms=settings.auth0_algorithms,
            audience=settings.auth0_audience,
            issuer=_issuer(settings),
        )
    except JWTError as exc:
        raise AuthenticationError("Token verification failed") from exc
    return claims
