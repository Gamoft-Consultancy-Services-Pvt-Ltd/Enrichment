"""Integration tests — POST /channels/erasure-request endpoint (COMP-303).

Uses ASGITransport + AsyncClient with monkeypatched token verification,
same pattern as test_file_upload_golden_path.py.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import auth.token as token_module
from auth.models import User
from auth.schemas import Role
from core.db import get_session
from main import app
from modules.lead_ingestion.db.models import Lead
from shared.tenant import service as tenant_service
from shared.tenant.schemas import BusinessType, TenantCreate

_NS = "https://leadengine/"


async def _seed_lead(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    phone: str = "+919876543210",
    email: str = "user@example.com",
) -> Lead:
    lead = Lead(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        pipeline_stage="captured",
        source_channel="file_upload",
        full_name="Test User",
        phone=phone,
        email=email,
        location="Mumbai",
        raw_event_json={"original": "event"},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(lead)
    await session.flush()
    return lead


@pytest.fixture
async def client(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession
) -> AsyncIterator[tuple[AsyncClient, uuid.UUID]]:
    """Async client authenticated as a tenant user with a seeded lead."""
    tenant = await tenant_service.create_tenant(
        session,
        TenantCreate(
            company_name="Erasure Endpoint Test Co",
            primary_contact_name="Admin",
            primary_contact_email="admin@erasureep.com",
            business_type=BusinessType.B2C,
            website_url="https://erasureep.com",  # type: ignore[arg-type]
            pan="AAACX1234C",
            pan_holder_name="Test Holder Pvt Ltd",
            pan_dob="01/04/2019",
            consent=True,
        ),
    )
    user = User(
        auth0_sub="auth0|erasure-ep-test",
        email="admin@erasureep.com",
        role=Role.TENANT,
        tenant_id=tenant.id,
    )
    session.add(user)
    await session.commit()

    def fake_verify(token: str) -> dict[str, Any]:
        if token == "tenant-token":
            return {
                "sub": "auth0|erasure-ep-test",
                f"{_NS}email": "admin@erasureep.com",
                f"{_NS}role": "TENANT",
            }
        from core.exceptions import AuthenticationError

        raise AuthenticationError("bad token")

    monkeypatch.setattr(token_module, "verify_token", fake_verify)

    async def _use_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _use_test_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, tenant.id
    app.dependency_overrides.clear()


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer tenant-token"}


async def test_erasure_endpoint_golden_path(
    client: tuple[AsyncClient, uuid.UUID], session: AsyncSession
) -> None:
    """POST /channels/erasure-request erases the matching lead and returns count + ids."""
    ac, tenant_id = client
    lead = await _seed_lead(session, tenant_id=tenant_id, phone="+919876543210")
    lead_id = lead.id
    await session.commit()

    resp = await ac.post(
        "/channels/erasure-request",
        headers=_auth(),
        json={"identifier_type": "phone", "identifier": "+919876543210"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["erased_lead_count"] == 1
    assert str(lead_id) in body["erased_lead_ids"]
    assert body["event"] is not None
    assert body["event"]["identifier_type"] == "phone"

    session.expire_all()
    refreshed = await session.get(Lead, lead_id)
    assert refreshed is not None
    assert refreshed.pipeline_stage == "erased"
    assert refreshed.phone is None


async def test_erasure_endpoint_invalid_identifier_type_returns_400(
    client: tuple[AsyncClient, uuid.UUID],
) -> None:
    ac, _ = client
    resp = await ac.post(
        "/channels/erasure-request",
        headers=_auth(),
        json={"identifier_type": "ssn", "identifier": "123-45-6789"},
    )
    assert resp.status_code == 400
    assert "identifier_type" in resp.json()["detail"]


async def test_erasure_endpoint_unauthenticated_returns_401(
    client: tuple[AsyncClient, uuid.UUID],
) -> None:
    ac, _ = client
    resp = await ac.post(
        "/channels/erasure-request",
        json={"identifier_type": "phone", "identifier": "+919876543210"},
    )
    assert resp.status_code == 401
