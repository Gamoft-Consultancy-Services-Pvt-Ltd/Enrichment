"""Integration tests for POST /onboarding.

Drives the ASGI app with httpx.AsyncClient on the test's event loop and overrides
get_session to use the truncating `session` fixture — so the endpoint and the
assertions share one session (clean per test, single event loop).
"""

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import auth.token as token_module
import clients.pan_client as pan_client_module
from auth.models import User
from core.db import get_session
from core.queue import get_arq_pool
from main import app
from shared.tenant.schemas import OnboardingStatus
from shared.tenant.service import set_onboarding_status
from tests.helpers import build_settings

NS = "https://leadengine/"

_BUSINESS = {
    "company_name": "Acme",
    "primary_contact_name": "Ada",
    "primary_contact_email": "ada@acme.com",
    "business_type": "B2B",
    "website_url": "https://acme.com",
    "timezone": "UTC",
    "language_preference": "en",
    "pan": "AAACX1234C",
    "pan_holder_name": "Acme Private Limited",
    "pan_dob": "01/04/2019",
    "consent": True,
}


@pytest.fixture
async def client(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession
) -> AsyncIterator[AsyncClient]:
    """Async client for the app, with token verification stubbed, DB session
    overridden, and ARQ pool mocked. 'tenant-token' is a logged-in TENANT user
    who has not onboarded yet (no tenant_id claim).

    pan_use_mock is explicitly pinned to True here so a stray PAN_USE_MOCK=false
    env var can never push these tests onto the live network.
    """

    def fake_verify(token: str) -> dict[str, Any]:
        if token == "tenant-token":
            return {"sub": "auth0|newtenant", f"{NS}email": "ada@acme.com", f"{NS}role": "TENANT"}
        from core.exceptions import AuthenticationError

        raise AuthenticationError("bad token")

    monkeypatch.setattr(token_module, "verify_token", fake_verify)
    # Pin the mock so no real Sandbox network call can happen regardless of env vars.
    monkeypatch.setattr(
        pan_client_module, "get_settings", lambda: build_settings(pan_use_mock=True)
    )

    async def _use_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    mock_pool = AsyncMock()
    mock_pool.enqueue_job = AsyncMock()

    app.dependency_overrides[get_session] = _use_test_session
    app.dependency_overrides[get_arq_pool] = lambda: mock_pool
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


async def test_second_onboarding_is_conflict(client: AsyncClient, session: AsyncSession) -> None:
    # Create the tenant, then simulate the pipeline having advanced past PENDING
    # so the endpoint treats the repeat as a genuine conflict (not a retry).
    first = await client.post("/onboarding", headers=_auth(), json=_BUSINESS)
    assert first.status_code == 200
    # Advance the onboarding status past PENDING so the 409 branch fires.
    await set_onboarding_status(session, UUID(first.json()["id"]), OnboardingStatus.RUNNING)
    second = await client.post("/onboarding", headers=_auth(), json=_BUSINESS)
    assert second.status_code == 409


async def test_onboarding_without_token_is_401(client: AsyncClient) -> None:
    resp = await client.post("/onboarding", json=_BUSINESS)
    assert resp.status_code == 401


async def test_onboarding_returns_pending_onboarding_status(client: AsyncClient) -> None:
    resp = await client.post("/onboarding", headers=_auth(), json=_BUSINESS)
    assert resp.status_code == 200
    assert resp.json()["onboarding_status"] == "PENDING"


async def test_onboarding_enqueues_pipeline_job(client: AsyncClient) -> None:
    mock_pool = app.dependency_overrides[get_arq_pool]()
    resp = await client.post("/onboarding", headers=_auth(), json=_BUSINESS)
    assert resp.status_code == 200
    mock_pool.enqueue_job.assert_awaited_once()
    assert mock_pool.enqueue_job.call_args.args[0] == "run_onboarding_pipeline"


async def test_onboarding_with_unverified_pan_is_422_and_creates_nothing(
    client: AsyncClient, session: AsyncSession
) -> None:
    bad = {**_BUSINESS, "pan": "AAAAA0000A"}  # mock sentinel -> match fails
    resp = await client.post("/onboarding", headers=_auth(), json=bad)
    assert resp.status_code == 422
    user = (
        await session.execute(select(User).where(User.auth0_sub == "auth0|newtenant"))
    ).scalar_one()
    assert user.tenant_id is None


async def test_onboarding_marks_tenant_kyb_verified(client: AsyncClient) -> None:
    resp = await client.post("/onboarding", headers=_auth(), json=_BUSINESS)
    assert resp.status_code == 200
    assert resp.json()["kyb_status"] == "VERIFIED"


async def test_onboarding_reenqueues_pending_tenant_instead_of_409(
    client: AsyncClient,
) -> None:
    mock_pool = app.dependency_overrides[get_arq_pool]()
    # First call onboards the user (tenant created, status PENDING).
    first = await client.post("/onboarding", headers=_auth(), json=_BUSINESS)
    assert first.status_code == 200
    # Second call (e.g. user retried after a prior enqueue blip): the tenant is still
    # PENDING, so we re-enqueue and return 200 rather than 409.
    second = await client.post("/onboarding", headers=_auth(), json=_BUSINESS)
    assert second.status_code == 200
    assert second.json()["kyb_status"] == "VERIFIED"
    # Both calls must have triggered an enqueue — the recovery branch must actually
    # re-enqueue, not just return 200 silently.
    assert mock_pool.enqueue_job.await_count == 2
