"""Integration tests — PROD-003: RBAC enforcement via require_tenant_user.

Verifies that tenant-only endpoints return 403 when a platform_admin token
is presented, and 200 for valid tenant tokens (regression).
"""

import io
import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import auth.token as token_module
from auth.models import User
from auth.schemas import Role
from core.db import get_session
from core.queue import get_arq_pool
from main import app
from shared.tenant import service as tenant_service
from shared.tenant.schemas import BusinessType, TenantCreate

_NS = "https://leadengine/"


async def _seed_tenant(session: AsyncSession) -> uuid.UUID:
    tenant = await tenant_service.create_tenant(
        session,
        TenantCreate(
            company_name=f"RBAC Co {uuid.uuid4().hex[:6]}",
            primary_contact_name="Admin",
            primary_contact_email=f"admin_{uuid.uuid4().hex[:6]}@rbac.com",
            business_type=BusinessType.B2B,
            website_url="https://rbac.com",  # type: ignore[arg-type]
            pan="AAACX1234C",
            pan_holder_name="Test Holder Pvt Ltd",
            pan_dob="01/04/2019",
            consent=True,
        ),
    )
    await session.commit()
    return tenant.id


def _make_client(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
    *,
    role: Role,
    tenant_id: uuid.UUID | None,
    user_sub: str,
    user_email: str,
) -> AsyncClient:
    def fake_verify(token: str) -> dict[str, object]:
        if token == "good":
            claims: dict[str, object] = {
                "sub": user_sub,
                f"{_NS}email": user_email,
                f"{_NS}role": role.value,
            }
            if tenant_id is not None:
                claims[f"{_NS}tenant_id"] = str(tenant_id)
            return claims
        from core.exceptions import AuthenticationError

        raise AuthenticationError("bad token")

    monkeypatch.setattr(token_module, "verify_token", fake_verify)

    async def _use_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    mock_pool = AsyncMock()
    app.dependency_overrides[get_session] = _use_test_session
    app.dependency_overrides[get_arq_pool] = lambda: mock_pool
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def tenant_client(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession
) -> AsyncIterator[tuple[AsyncClient, uuid.UUID]]:
    tenant_id = await _seed_tenant(session)
    user = User(
        auth0_sub="auth0|rbac-tenant",
        email="tenant@rbac.com",
        role=Role.TENANT,
        tenant_id=tenant_id,
    )
    session.add(user)
    await session.commit()

    ac = _make_client(
        monkeypatch,
        session,
        role=Role.TENANT,
        tenant_id=tenant_id,
        user_sub="auth0|rbac-tenant",
        user_email="tenant@rbac.com",
    )
    async with ac as client:
        yield client, tenant_id
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_arq_pool, None)


@pytest.fixture
async def admin_client(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession
) -> AsyncIterator[AsyncClient]:
    user = User(
        auth0_sub="auth0|rbac-admin",
        email="admin@platform.com",
        role=Role.PLATFORM_ADMIN,
        tenant_id=None,
    )
    session.add(user)
    await session.commit()

    ac = _make_client(
        monkeypatch,
        session,
        role=Role.PLATFORM_ADMIN,
        tenant_id=None,
        user_sub="auth0|rbac-admin",
        user_email="admin@platform.com",
    )
    async with ac as client:
        yield client
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_arq_pool, None)


# ---------------------------------------------------------------------------
# File upload — tenant-only endpoint
# ---------------------------------------------------------------------------


async def test_file_upload_platform_admin_gets_403(admin_client: AsyncClient) -> None:
    csv_bytes = b"name,phone\nTest User,+919876543210"
    resp = await admin_client.post(
        "/channels/inbound/file-upload",
        files={"file": ("leads.csv", io.BytesIO(csv_bytes), "text/csv")},
        headers={"Authorization": "Bearer good"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "tenant_role_required"


async def test_file_upload_tenant_user_is_allowed(
    tenant_client: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = tenant_client
    csv_bytes = b"name,phone\nTest User,+919876543210"
    resp = await client.post(
        "/channels/inbound/file-upload",
        files={"file": ("leads.csv", io.BytesIO(csv_bytes), "text/csv")},
        headers={"Authorization": "Bearer good"},
    )
    # 200 or 400 (pre-flight) is fine — either proves auth passed
    assert resp.status_code in (200, 400)
    assert "tenant_role_required" not in resp.text


# ---------------------------------------------------------------------------
# Erasure request — tenant-only endpoint
# ---------------------------------------------------------------------------


async def test_erasure_platform_admin_gets_403(admin_client: AsyncClient) -> None:
    resp = await admin_client.post(
        "/channels/erasure-request",
        json={"identifier_type": "phone", "identifier": "+919876543210"},
        headers={"Authorization": "Bearer good"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "tenant_role_required"


async def test_erasure_tenant_user_is_allowed(
    tenant_client: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = tenant_client
    resp = await client.post(
        "/channels/erasure-request",
        json={"identifier_type": "phone", "identifier": "+910000000000"},
        headers={"Authorization": "Bearer good"},
    )
    assert resp.status_code == 200
    assert resp.json()["erased_lead_count"] == 0


# ---------------------------------------------------------------------------
# OAuth initiation — tenant-only endpoints
# ---------------------------------------------------------------------------


async def test_facebook_oauth_initiation_platform_admin_gets_403(
    admin_client: AsyncClient,
) -> None:
    resp = await admin_client.get(
        "/channels/oauth/facebook",
        headers={"Authorization": "Bearer good"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


async def test_instagram_oauth_initiation_platform_admin_gets_403(
    admin_client: AsyncClient,
) -> None:
    resp = await admin_client.get(
        "/channels/oauth/instagram",
        headers={"Authorization": "Bearer good"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


async def test_embedded_signup_platform_admin_gets_403(
    admin_client: AsyncClient,
) -> None:
    resp = await admin_client.post(
        "/channels/embedded-signup/callback",
        json={"code": "some-code"},
        headers={"Authorization": "Bearer good"},
    )
    assert resp.status_code == 403
