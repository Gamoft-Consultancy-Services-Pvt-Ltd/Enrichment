"""Integration tests for POST /channels/inbound/file-upload.

Uses ASGITransport + the shared `session` fixture (same pattern as
test_onboarding_endpoint.py).  The DB session and the app endpoint share one
session so assertions can query what the endpoint wrote.
"""

import csv
import io
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import auth.token as token_module
from auth.models import User
from auth.schemas import Role
from core.db import get_session
from core.queue import get_arq_pool
from main import app
from modules.lead_ingestion.db.models import Lead
from shared.tenant import service as tenant_service
from shared.tenant.schemas import BusinessType, TenantCreate
from shared.tenant_config import service as config_service
from shared.tenant_config.models import TenantConfig
from shared.tenant_config.schemas import ConfigStatus, TenantConfigCreate

_NS = "https://leadengine/"
_CSV_FIXTURE = Path(__file__).parents[2] / "fixtures" / "lead_ingestion" / "csv_01.csv"


def _config_payload() -> TenantConfigCreate:
    return TenantConfigCreate.model_validate(
        {
            "business_profile": {"summary": "Real estate leads"},
            "icp": {"summary": "Mid-market home buyers"},
            "signals": [
                {"id": "fit_1", "dimension": "FIT", "question": "In target city?"},
                {"id": "intent_1", "dimension": "INTENT", "question": "Actively searching?"},
                {"id": "eng_1", "dimension": "ENGAGEMENT", "question": "Replied to ad?"},
                {"id": "beh_1", "dimension": "BEHAVIOUR", "question": "Visited site?"},
                {"id": "ctx_1", "dimension": "CONTEXT", "question": "Recent life event?"},
            ],
            "weights": {
                "fit": 0.2,
                "intent": 0.2,
                "engagement": 0.2,
                "behaviour": 0.2,
                "context": 0.2,
            },
            "thresholds": {"hot": 80, "warm": 55},
        }
    )


async def _make_tenant_with_config(session: AsyncSession) -> UUID:
    tenant = await tenant_service.create_tenant(
        session,
        TenantCreate(
            company_name="Upload Test Co",
            primary_contact_name="Priya",
            primary_contact_email="priya@uploadco.com",
            business_type=BusinessType.B2C,
            website_url="https://uploadco.com",  # type: ignore[arg-type]
        ),
    )
    await config_service.create_active(session, tenant.id, _config_payload())
    return tenant.id


async def _make_tenant_no_config(session: AsyncSession) -> UUID:
    tenant = await tenant_service.create_tenant(
        session,
        TenantCreate(
            company_name="No Config Co",
            primary_contact_name="Ravi",
            primary_contact_email="ravi@noconfig.com",
            business_type=BusinessType.B2B,
            website_url="https://noconfig.com",  # type: ignore[arg-type]
        ),
    )
    return tenant.id


@pytest.fixture
async def client(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession
) -> AsyncIterator[tuple[AsyncClient, UUID]]:
    """Async client with a pre-onboarded tenant user and active config."""
    tenant_id = await _make_tenant_with_config(session)

    user = User(
        auth0_sub="auth0|upload-test",
        email="priya@uploadco.com",
        role=Role.TENANT,
        tenant_id=tenant_id,
    )
    session.add(user)
    await session.commit()

    def fake_verify(token: str) -> dict[str, Any]:
        if token == "tenant-token":
            return {
                "sub": "auth0|upload-test",
                f"{_NS}email": "priya@uploadco.com",
                f"{_NS}role": "TENANT",
            }
        from core.exceptions import AuthenticationError

        raise AuthenticationError("bad token")

    monkeypatch.setattr(token_module, "verify_token", fake_verify)

    async def _use_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    mock_pool = AsyncMock()
    mock_pool.enqueue_job = AsyncMock(return_value=None)

    app.dependency_overrides[get_session] = _use_test_session
    app.dependency_overrides[get_arq_pool] = lambda: mock_pool

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, tenant_id
    app.dependency_overrides.clear()


@pytest.fixture
async def client_no_config(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession
) -> AsyncIterator[AsyncClient]:
    """Client whose tenant has NO TenantConfig (pre-flight should halt)."""
    tenant_id = await _make_tenant_no_config(session)

    user = User(
        auth0_sub="auth0|noconfig-test",
        email="ravi@noconfig.com",
        role=Role.TENANT,
        tenant_id=tenant_id,
    )
    session.add(user)
    await session.commit()

    def fake_verify(token: str) -> dict[str, Any]:
        if token == "tenant-token":
            return {
                "sub": "auth0|noconfig-test",
                f"{_NS}email": "ravi@noconfig.com",
                f"{_NS}role": "TENANT",
            }
        from core.exceptions import AuthenticationError

        raise AuthenticationError("bad token")

    monkeypatch.setattr(token_module, "verify_token", fake_verify)

    async def _use_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    mock_pool = AsyncMock()
    app.dependency_overrides[get_session] = _use_test_session
    app.dependency_overrides[get_arq_pool] = lambda: mock_pool

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def client_empty_signals(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession
) -> AsyncIterator[AsyncClient]:
    """Client whose tenant has a config with empty signals list."""
    tenant = await tenant_service.create_tenant(
        session,
        TenantCreate(
            company_name="Empty Signals Co",
            primary_contact_name="Meera",
            primary_contact_email="meera@emptysig.com",
            business_type=BusinessType.B2B,
            website_url="https://emptysig.com",  # type: ignore[arg-type]
        ),
    )
    # Insert directly bypassing TenantConfigCreate validation (which rejects empty signals).
    bad_config = TenantConfig(
        tenant_id=tenant.id,
        version=1,
        status=ConfigStatus.ACTIVE,
        business_profile={"summary": "test"},
        icp={"summary": "test"},
        signals=[],
        weights={"fit": 0.2, "intent": 0.2, "engagement": 0.2, "behaviour": 0.2, "context": 0.2},
        thresholds={"hot": 80, "warm": 55},
        activated_at=datetime.now(UTC),
    )
    session.add(bad_config)

    user = User(
        auth0_sub="auth0|emptysig-test",
        email="meera@emptysig.com",
        role=Role.TENANT,
        tenant_id=tenant.id,
    )
    session.add(user)
    await session.commit()

    def fake_verify(token: str) -> dict[str, Any]:
        if token == "tenant-token":
            return {
                "sub": "auth0|emptysig-test",
                f"{_NS}email": "meera@emptysig.com",
                f"{_NS}role": "TENANT",
            }
        from core.exceptions import AuthenticationError

        raise AuthenticationError("bad token")

    monkeypatch.setattr(token_module, "verify_token", fake_verify)

    async def _use_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    mock_pool = AsyncMock()
    app.dependency_overrides[get_session] = _use_test_session
    app.dependency_overrides[get_arq_pool] = lambda: mock_pool

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer tenant-token"}


def _upload(content: bytes, filename: str = "leads.csv", mime: str = "text/csv") -> dict[str, Any]:
    return {"file": (filename, content, mime)}


async def test_csv_standard_headers_map_to_canonical_fields(
    client: tuple[AsyncClient, UUID], session: AsyncSession
) -> None:
    ac, tenant_id = client
    csv_bytes = _CSV_FIXTURE.read_bytes()

    resp = await ac.post("/channels/inbound/file-upload", headers=_auth(), files=_upload(csv_bytes))
    assert resp.status_code == 200

    body = resp.json()
    assert body["mode"] == "sync"
    assert body["row_count"] == 3
    assert len(body["lead_ids"]) == 3

    leads = (
        (
            await session.execute(
                select(Lead).where(Lead.tenant_id == tenant_id).order_by(Lead.created_at)
            )
        )
        .scalars()
        .all()
    )

    alice = next(ld for ld in leads if ld.full_name == "Alice Sharma")
    assert alice.phone == "+919876543210"
    assert alice.email == "alice@example.com"
    assert alice.location == "Mumbai"
    assert alice.extra_fields == {"Budget Range": "5-10L", "Notes": "Referred by Raj"}
    assert alice.pipeline_stage == "captured"

    bob = next(ld for ld in leads if ld.full_name == "Bob Kumar")
    assert bob.email == "bob@example.com"
    assert bob.phone is None
    assert bob.pipeline_stage == "captured"


async def test_identity_less_row_creates_blocked_lead(
    client: tuple[AsyncClient, UUID], session: AsyncSession
) -> None:
    ac, tenant_id = client
    csv_bytes = _CSV_FIXTURE.read_bytes()

    resp = await ac.post("/channels/inbound/file-upload", headers=_auth(), files=_upload(csv_bytes))
    assert resp.status_code == 200

    blocked = (
        (
            await session.execute(
                select(Lead).where(
                    Lead.tenant_id == tenant_id,
                    Lead.pre_flight_block_reason == "insufficient_identity_fields",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(blocked) == 1
    assert blocked[0].pipeline_stage == "pre_flight_blocked"


async def test_unsupported_file_type_returns_422(
    client: tuple[AsyncClient, UUID],
) -> None:
    ac, _ = client
    resp = await ac.post(
        "/channels/inbound/file-upload",
        headers=_auth(),
        files=_upload(b"fake docx content", filename="leads.docx", mime="application/octet-stream"),
    )
    assert resp.status_code == 422


async def test_file_over_max_rows_returns_422(
    client: tuple[AsyncClient, UUID],
) -> None:
    ac, _ = client
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["Contact Name", "Mobile"])
    writer.writeheader()
    for i in range(5_001):
        writer.writerow({"Contact Name": f"Person {i}", "Mobile": f"+91{i:010d}"})
    content = buf.getvalue().encode()

    resp = await ac.post(
        "/channels/inbound/file-upload",
        headers=_auth(),
        files=_upload(content),
    )
    assert resp.status_code == 422
    assert "5001" in resp.json()["detail"]


async def test_preflight_halt_no_config_returns_400(client_no_config: AsyncClient) -> None:
    csv_bytes = _CSV_FIXTURE.read_bytes()
    resp = await client_no_config.post(
        "/channels/inbound/file-upload", headers=_auth(), files=_upload(csv_bytes)
    )
    assert resp.status_code == 400
    assert "no_active_config" in resp.json()["detail"]


async def test_preflight_halt_empty_signals_returns_400(
    client_empty_signals: AsyncClient,
) -> None:
    csv_bytes = _CSV_FIXTURE.read_bytes()
    resp = await client_empty_signals.post(
        "/channels/inbound/file-upload", headers=_auth(), files=_upload(csv_bytes)
    )
    assert resp.status_code == 400
    assert "empty_signals" in resp.json()["detail"]


async def test_unauthenticated_request_returns_401(
    client: tuple[AsyncClient, UUID],
) -> None:
    ac, _ = client
    resp = await ac.post(
        "/channels/inbound/file-upload",
        files=_upload(_CSV_FIXTURE.read_bytes()),
    )
    assert resp.status_code == 401


async def test_large_file_dispatched_async(
    client: tuple[AsyncClient, UUID],
    session: AsyncSession,
) -> None:
    """Files with > 100 rows are enqueued as an ARQ job, not processed inline."""
    ac, _ = client
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["Contact Name", "Mobile"])
    writer.writeheader()
    for i in range(101):
        writer.writerow({"Contact Name": f"Person {i}", "Mobile": f"+91{i:010d}"})
    content = buf.getvalue().encode()

    resp = await ac.post(
        "/channels/inbound/file-upload",
        headers=_auth(),
        files=_upload(content),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "async"
    assert body["row_count"] == 101
    assert "lead_ids" not in body
