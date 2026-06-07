"""Integration tests for shared.tenant_config.service against a real Postgres."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import ConflictError, NotFoundError
from shared.tenant import service as tenant_service
from shared.tenant.schemas import BusinessType, TenantCreate
from shared.tenant_config import service
from shared.tenant_config.schemas import ConfigStatus, TenantConfigCreate


def _signals() -> list[dict[str, str]]:
    return [
        {"id": "fit_1", "dimension": "FIT", "question": "In target industry?"},
        {"id": "intent_1", "dimension": "INTENT", "question": "Visited pricing?"},
        {"id": "eng_1", "dimension": "ENGAGEMENT", "question": "Opened last email?"},
        {"id": "beh_1", "dimension": "BEHAVIOUR", "question": "Requested a demo?"},
        {"id": "ctx_1", "dimension": "CONTEXT", "question": "Raised funding recently?"},
    ]


def _payload() -> TenantConfigCreate:
    return TenantConfigCreate.model_validate(
        {
            "business_profile": {"summary": "B2B SaaS"},
            "icp": {"summary": "Mid-market SaaS in APAC"},
            "signals": _signals(),
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


async def _make_tenant(session: AsyncSession) -> UUID:
    tenant = await tenant_service.create_tenant(
        session,
        TenantCreate(
            company_name="Gamoft",
            primary_contact_name="Asha",
            primary_contact_email="asha@gamoft.com",
            business_type=BusinessType.B2B,
        ),
    )
    return tenant.id


async def test_create_draft_assigns_version_one(session: AsyncSession) -> None:
    tenant_id = await _make_tenant(session)

    draft = await service.create_draft(session, tenant_id, _payload())

    assert draft.version == 1
    assert draft.status is ConfigStatus.DRAFT
    assert draft.tenant_id == tenant_id
    assert draft.activated_at is None
    assert draft.weights.fit == 0.2


async def test_second_create_draft_while_draft_exists_raises_conflict(
    session: AsyncSession,
) -> None:
    tenant_id = await _make_tenant(session)
    await service.create_draft(session, tenant_id, _payload())

    with pytest.raises(ConflictError):
        await service.create_draft(session, tenant_id, _payload())


async def test_get_active_config_returns_none_when_no_active(session: AsyncSession) -> None:
    tenant_id = await _make_tenant(session)
    await service.create_draft(session, tenant_id, _payload())  # only a draft, not active

    assert await service.get_active_config(session, tenant_id) is None


async def test_get_active_config_missing_tenant_returns_none(session: AsyncSession) -> None:
    assert await service.get_active_config(session, uuid4()) is None
