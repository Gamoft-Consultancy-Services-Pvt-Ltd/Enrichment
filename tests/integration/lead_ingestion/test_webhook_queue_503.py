"""Integration tests — PROD-001: POST /channels/webhook returns 503 when ARQ enqueue fails.

Covers all three routing paths (WhatsApp, Facebook DM, Lead Ads) with a mocked
ARQ pool that raises on enqueue_job to simulate Redis unavailability.
"""

import hashlib
import hmac
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.db import get_session
from core.queue import get_arq_pool
from main import app
from shared.channels.models import ChannelConnection
from shared.tenant import service as tenant_service
from shared.tenant.schemas import BusinessType, TenantCreate

_NS = "https://leadengine/"


def _hmac_sig(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def _seed_tenant(session: AsyncSession) -> uuid.UUID:
    tenant = await tenant_service.create_tenant(
        session,
        TenantCreate(
            company_name=f"Queue503 Co {uuid.uuid4().hex[:6]}",
            primary_contact_name="Admin",
            primary_contact_email=f"admin_{uuid.uuid4().hex[:6]}@q503.com",
            business_type=BusinessType.B2B,
            website_url="https://q503.com",  # type: ignore[arg-type]
        ),
    )
    await session.commit()
    return tenant.id


async def _seed_whatsapp_connection(
    session: AsyncSession, tenant_id: uuid.UUID, phone_number_id: str
) -> ChannelConnection:
    conn = ChannelConnection(
        tenant_id=tenant_id,
        channel_type="whatsapp",
        status="active",
        credentials_encrypted=b"fake",
        connection_metadata={"phone_number_id": phone_number_id},
    )
    session.add(conn)
    await session.commit()
    return conn


async def _seed_facebook_connection(
    session: AsyncSession, tenant_id: uuid.UUID, page_id: str
) -> ChannelConnection:
    conn = ChannelConnection(
        tenant_id=tenant_id,
        channel_type="facebook",
        status="active",
        credentials_encrypted=b"fake",
        connection_metadata={"page_id": page_id},
    )
    session.add(conn)
    await session.commit()
    return conn


@pytest.fixture
async def failing_arq_client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """ASGITransport client whose ARQ pool raises on every enqueue_job call."""
    mock_pool = AsyncMock()
    mock_pool.enqueue_job.side_effect = OSError("Redis connection refused")

    async def _use_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_arq_pool] = lambda: mock_pool
    app.dependency_overrides[get_session] = _use_test_session
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_arq_pool, None)
        app.dependency_overrides.pop(get_session, None)


def _post_webhook(
    payload: Any, *, secret: str
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    return body, {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": _hmac_sig(body, secret),
    }


async def test_whatsapp_webhook_returns_503_on_queue_failure(
    failing_arq_client: AsyncClient, session: AsyncSession
) -> None:
    tenant_id = await _seed_tenant(session)
    phone_number_id = f"pn-{uuid.uuid4().hex[:8]}"
    await _seed_whatsapp_connection(session, tenant_id, phone_number_id)

    payload = {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": phone_number_id},
            "messages": [{"id": f"wamid.{uuid.uuid4().hex}", "type": "text",
                          "text": {"body": "Hello"}, "from": "919876543210"}],
        }}]}],
    }
    secret = get_settings().meta_app_secret
    body, headers = _post_webhook(payload, secret=secret)

    resp = await failing_arq_client.post("/channels/webhook", content=body, headers=headers)
    assert resp.status_code == 503
    assert resp.json()["detail"] == "queue_unavailable"


async def test_facebook_dm_webhook_returns_503_on_queue_failure(
    failing_arq_client: AsyncClient, session: AsyncSession
) -> None:
    tenant_id = await _seed_tenant(session)
    page_id = f"pg-{uuid.uuid4().hex[:8]}"
    await _seed_facebook_connection(session, tenant_id, page_id)

    payload = {
        "object": "page",
        "entry": [{"id": page_id, "changes": [{"field": "messages"}]}],
    }
    secret = get_settings().meta_app_secret
    body, headers = _post_webhook(payload, secret=secret)

    resp = await failing_arq_client.post("/channels/webhook", content=body, headers=headers)
    assert resp.status_code == 503
    assert resp.json()["detail"] == "queue_unavailable"


async def test_lead_ad_webhook_returns_503_on_queue_failure(
    failing_arq_client: AsyncClient, session: AsyncSession
) -> None:
    tenant_id = await _seed_tenant(session)
    page_id = f"pg-{uuid.uuid4().hex[:8]}"
    await _seed_facebook_connection(session, tenant_id, page_id)

    leadgen_id = uuid.uuid4().hex
    payload = {
        "object": "page",
        "entry": [{"id": page_id, "changes": [{"field": "leadgen",
                                                "value": {"leadgen_id": leadgen_id}}]}],
    }
    secret = get_settings().meta_app_secret
    body, headers = _post_webhook(payload, secret=secret)

    resp = await failing_arq_client.post("/channels/webhook", content=body, headers=headers)
    assert resp.status_code == 503
    assert resp.json()["detail"] == "queue_unavailable"
