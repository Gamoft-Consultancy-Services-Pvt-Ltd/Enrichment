"""Integration tests for POST /onboarding.

Drives the ASGI app with httpx.AsyncClient on the test's event loop and overrides
get_session to use the truncating `session` fixture — so the endpoint and the
assertions share one session (clean per test, single event loop).
"""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import auth.token as token_module
from auth.models import User
from core.db import get_session
from main import app

NS = "https://leadengine/"

_BUSINESS = {
    "company_name": "Acme",
    "primary_contact_name": "Ada",
    "primary_contact_email": "ada@acme.com",
    "business_type": "B2B",
    "timezone": "UTC",
    "language_preference": "en",
}


@pytest.fixture
async def client(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession
) -> AsyncIterator[AsyncClient]:
    """Async client for the app, with token verification stubbed and the DB session
    overridden to the per-test (truncated) session. 'tenant-token' is a logged-in
    TENANT user who has not onboarded yet (no tenant_id claim)."""

    def fake_verify(token: str) -> dict[str, Any]:
        if token == "tenant-token":
            return {"sub": "auth0|newtenant", "email": "ada@acme.com", f"{NS}role": "TENANT"}
        from core.exceptions import AuthenticationError

        raise AuthenticationError("bad token")

    monkeypatch.setattr(token_module, "verify_token", fake_verify)

    async def _use_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer tenant-token"}


async def test_onboarding_creates_tenant_and_links_user(
    client: AsyncClient, session: AsyncSession
) -> None:
    # First touch /me so the user row exists with no tenant.
    me = await client.get("/me", headers=_auth())
    assert me.status_code == 200
    assert me.json()["tenant_id"] is None

    resp = await client.post("/onboarding", headers=_auth(), json=_BUSINESS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["company_name"] == "Acme"
    assert body["business_type"] == "B2B"
    assert body["status"] == "CREATED"

    # The user is now linked to the created tenant.
    user = (
        await session.execute(select(User).where(User.auth0_sub == "auth0|newtenant"))
    ).scalar_one()
    assert str(user.tenant_id) == body["id"]


async def test_me_reflects_tenant_after_onboarding(client: AsyncClient) -> None:
    await client.post("/onboarding", headers=_auth(), json=_BUSINESS)
    me = await client.get("/me", headers=_auth())
    assert me.status_code == 200
    assert me.json()["tenant_id"] is not None


async def test_second_onboarding_is_conflict(client: AsyncClient) -> None:
    first = await client.post("/onboarding", headers=_auth(), json=_BUSINESS)
    assert first.status_code == 200
    second = await client.post("/onboarding", headers=_auth(), json=_BUSINESS)
    assert second.status_code == 409


async def test_onboarding_without_token_is_401(client: AsyncClient) -> None:
    resp = await client.post("/onboarding", json=_BUSINESS)
    assert resp.status_code == 401
