"""Integration test: the synchronous (<=100 row) file-upload path must enqueue
`run_lead_pipeline` for each successfully captured lead, mirroring the worker
jobs' `_dispatch_enrichment` contract. Blocked (identity-less) rows must not
trigger an enrichment enqueue.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, call
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
            company_name="Enrichment Dispatch Co",
            primary_contact_name="Priya",
            primary_contact_email="priya@enrichdispatch.com",
            business_type=BusinessType.B2C,
            website_url="https://enrichdispatch.com",  # type: ignore[arg-type]
            pan="AAACX1234C",
            pan_holder_name="Test Holder Pvt Ltd",
            pan_dob="01/04/2019",
            consent=True,
        ),
    )
    await config_service.create_active(session, tenant.id, _config_payload())
    return tenant.id


@pytest.fixture
async def client(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession
) -> AsyncIterator[tuple[AsyncClient, UUID, AsyncMock]]:
    """Async client with a pre-onboarded tenant user, active config, and a mock ARQ pool."""
    tenant_id = await _make_tenant_with_config(session)

    user = User(
        auth0_sub="auth0|enrich-dispatch-test",
        email="priya@enrichdispatch.com",
        role=Role.TENANT,
        tenant_id=tenant_id,
    )
    session.add(user)
    await session.commit()

    def fake_verify(token: str) -> dict[str, Any]:
        if token == "tenant-token":
            return {
                "sub": "auth0|enrich-dispatch-test",
                f"{_NS}email": "priya@enrichdispatch.com",
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
        yield ac, tenant_id, mock_pool
    app.dependency_overrides.clear()


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer tenant-token"}


def _upload(content: bytes, filename: str = "leads.csv", mime: str = "text/csv") -> dict[str, Any]:
    return {"file": (filename, content, mime)}


async def test_sync_upload_enqueues_run_lead_pipeline_per_captured_lead(
    client: tuple[AsyncClient, UUID, AsyncMock], session: AsyncSession
) -> None:
    """Each row that produces a real (non-blocked) Lead must enqueue run_lead_pipeline,
    idempotency-keyed by enrich:{lead_id} -- matching workers/jobs/lead_ingestion.py's
    _dispatch_enrichment contract.
    """
    ac, tenant_id, mock_pool = client
    csv_bytes = _CSV_FIXTURE.read_bytes()

    resp = await ac.post("/channels/inbound/file-upload", headers=_auth(), files=_upload(csv_bytes))
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "sync"

    leads = (
        (await session.execute(select(Lead).where(Lead.tenant_id == tenant_id))).scalars().all()
    )
    captured = [ld for ld in leads if ld.pipeline_stage == "captured"]
    blocked = [ld for ld in leads if ld.pipeline_stage == "pre_flight_blocked"]

    # Fixture has 2 captured leads (Alice, Bob) and 1 identity-less blocked row.
    assert len(captured) == 2
    assert len(blocked) == 1

    enrichment_calls = [
        c for c in mock_pool.enqueue_job.await_args_list if c.args[:1] == ("run_lead_pipeline",)
    ]
    assert len(enrichment_calls) == 2

    expected_job_ids = {f"enrich:{ld.id}" for ld in captured}
    actual_job_ids = {c.kwargs["_job_id"] for c in enrichment_calls}
    assert actual_job_ids == expected_job_ids

    # No enrichment enqueue for the blocked (identity-less) lead.
    for ld in blocked:
        assert call("run_lead_pipeline", _job_id=f"enrich:{ld.id}") not in [
            call(*c.args, **c.kwargs) for c in enrichment_calls
        ]
