"""Unit tests for auth.token — JWT verification against a mocked JWKS, no network."""

from collections.abc import Iterator

import httpx
import pytest
import respx

from core.exceptions import AuthenticationError
from tests.helpers import build_settings
from tests.helpers.auth import make_keypair, make_token

DOMAIN = "test.auth0.com"
AUDIENCE = "api://leadengine"
ISSUER = f"https://{DOMAIN}/"
JWKS_URL = f"https://{DOMAIN}/.well-known/jwks.json"


@pytest.fixture(autouse=True)
def _settings_and_clean_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    import auth.token as token_module

    monkeypatch.setattr(
        token_module,
        "get_settings",
        lambda: build_settings(auth0_domain=DOMAIN, auth0_audience=AUDIENCE),
    )
    token_module._JWKS_CACHE.clear()
    yield
    token_module._JWKS_CACHE.clear()


def _claims() -> dict[str, str]:
    return {"sub": "auth0|abc", "email": "user@acme.com"}


@respx.mock
def test_valid_token_returns_claims() -> None:
    from auth.token import verify_token

    private_pem, public_jwk = make_keypair(kid="test-key")
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json={"keys": [public_jwk]}))
    token = make_token(
        private_pem, kid="test-key", audience=AUDIENCE, issuer=ISSUER, claims=_claims()
    )

    claims = verify_token(token)
    assert claims["sub"] == "auth0|abc"
    assert claims["email"] == "user@acme.com"


@respx.mock
def test_jwks_is_cached_after_first_fetch() -> None:
    from auth.token import verify_token

    private_pem, public_jwk = make_keypair(kid="test-key")
    route = respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json={"keys": [public_jwk]}))
    token = make_token(
        private_pem, kid="test-key", audience=AUDIENCE, issuer=ISSUER, claims=_claims()
    )

    verify_token(token)
    verify_token(token)
    assert route.call_count == 1


@respx.mock
def test_expired_token_raises() -> None:
    from auth.token import verify_token

    private_pem, public_jwk = make_keypair(kid="test-key")
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json={"keys": [public_jwk]}))
    token = make_token(
        private_pem,
        kid="test-key",
        audience=AUDIENCE,
        issuer=ISSUER,
        claims=_claims(),
        expires_in=-10,
    )

    with pytest.raises(AuthenticationError):
        verify_token(token)


@respx.mock
def test_wrong_audience_raises() -> None:
    from auth.token import verify_token

    private_pem, public_jwk = make_keypair(kid="test-key")
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json={"keys": [public_jwk]}))
    token = make_token(
        private_pem, kid="test-key", audience="api://wrong", issuer=ISSUER, claims=_claims()
    )

    with pytest.raises(AuthenticationError):
        verify_token(token)


@respx.mock
def test_wrong_issuer_raises() -> None:
    from auth.token import verify_token

    private_pem, public_jwk = make_keypair(kid="test-key")
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json={"keys": [public_jwk]}))
    token = make_token(
        private_pem,
        kid="test-key",
        audience=AUDIENCE,
        issuer="https://evil.example/",
        claims=_claims(),
    )

    with pytest.raises(AuthenticationError):
        verify_token(token)


@respx.mock
def test_bad_signature_raises() -> None:
    from auth.token import verify_token

    signing_pem, _ = make_keypair(kid="test-key")
    _, other_jwk = make_keypair(kid="test-key")  # different key, same kid
    respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json={"keys": [other_jwk]}))
    token = make_token(
        signing_pem, kid="test-key", audience=AUDIENCE, issuer=ISSUER, claims=_claims()
    )

    with pytest.raises(AuthenticationError):
        verify_token(token)


@respx.mock
def test_unknown_kid_triggers_one_refetch_then_fails() -> None:
    from auth.token import verify_token

    # Token signed by a key that is NOT in the served JWKS, under an unknown kid.
    unknown_pem, _ = make_keypair(kid="unknown-key")
    _, served_jwk = make_keypair(kid="known-key")
    route = respx.get(JWKS_URL).mock(return_value=httpx.Response(200, json={"keys": [served_jwk]}))
    token = make_token(
        unknown_pem, kid="unknown-key", audience=AUDIENCE, issuer=ISSUER, claims=_claims()
    )

    with pytest.raises(AuthenticationError):
        verify_token(token)
    assert route.call_count == 2  # initial + one forced refresh
