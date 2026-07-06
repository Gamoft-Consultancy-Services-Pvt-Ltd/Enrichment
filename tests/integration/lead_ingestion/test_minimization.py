"""Integration test for COMP-301 ST5 — sensitive field stripping at the file-upload boundary."""

import csv
import io
from collections.abc import AsyncIterator
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
from shared.tenant_config.schemas import TenantConfigCreate

_NS = "https://leadengine/"


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


@pytest.fixture
async def client(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession
) -> AsyncIterator[tuple[AsyncClient, UUID]]:
    tenant = await tenant_service.create_tenant(
        session,
        TenantCreate(
            company_name="Minimization Test Co",
            primary_contact_name="Priya",
            primary_contact_email="priya@mintest.com",
            business_type=BusinessType.B2C,
            website_url="https://mintest.com",  # type: ignore[arg-type]
            pan="AAACX1234C",
            pan_holder_name="Test Holder Pvt Ltd",
            pan_dob="01/04/2019",
            consent=True,
        ),
    )
    await config_service.create_active(session, tenant.id, _config_payload())

    user = User(
        auth0_sub="auth0|min-test",
        email="priya@mintest.com",
        role=Role.TENANT,
        tenant_id=tenant.id,
    )
    session.add(user)
    await session.commit()

    def fake_verify(token: str) -> dict[str, Any]:
        if token == "tenant-token":
            return {
                "sub": "auth0|min-test",
                f"{_NS}email": "priya@mintest.com",
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
        yield ac, tenant.id
    app.dependency_overrides.clear()


def _csv_with_pan() -> bytes:
    """CSV with: name + email (canonical identity), pan_number (sensitive), budget (clean)."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["name", "email", "pan_number", "budget"])
    writer.writeheader()
    writer.writerow(
        {
            "name": "Raj Sharma",
            "email": "raj@example.com",
            "pan_number": "ABCDE1234F",
            "budget": "50000",
        }
    )
    return buf.getvalue().encode()


async def test_pan_stripped_from_extra_fields(
    client: tuple[AsyncClient, UUID],
    session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ac, tenant_id = client
    csv_bytes = _csv_with_pan()

    resp = await ac.post(
        "/channels/inbound/file-upload",
        headers={"Authorization": "Bearer tenant-token"},
        files={"file": ("leads.csv", csv_bytes, "text/csv")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["row_count"] == 1

    leads = (await session.execute(select(Lead).where(Lead.tenant_id == tenant_id))).scalars().all()
    assert len(leads) == 1
    lead = leads[0]

    # Sensitive column must be absent from extra_fields
    assert lead.extra_fields is not None
    assert "pan_number" not in lead.extra_fields

    # Non-sensitive column must be present
    assert lead.extra_fields.get("budget") == "50000"

    # Lead was created successfully with the expected identity
    assert lead.full_name == "Raj Sharma"
    assert lead.pipeline_stage == "captured"

    # Structured log must mention the key name (never the value)
    captured = capsys.readouterr()
    assert "pan_number" in captured.err or "pan_number" in captured.out
    assert "ABCDE1234F" not in captured.err
    assert "ABCDE1234F" not in captured.out
