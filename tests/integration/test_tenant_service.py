"""Integration tests for shared.tenant.service against a real Postgres."""

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import ConflictError, NotFoundError
from shared.tenant import service
from shared.tenant.schemas import BusinessType, TenantCreate, TenantStatus


def _sample_create() -> TenantCreate:
    return TenantCreate(
        company_name="Gamoft",
        primary_contact_name="Asha",
        primary_contact_email="asha@gamoft.com",
        business_type=BusinessType.B2B,
        website_url="https://gamoft.com",  # type: ignore[arg-type]
    )


async def test_create_tenant_persists_with_created_status(session: AsyncSession) -> None:
    tenant = await service.create_tenant(session, _sample_create())

    assert tenant.id is not None
    assert tenant.status is TenantStatus.CREATED
    assert tenant.activated_at is None
    assert tenant.created_at is not None


async def test_get_tenant_returns_the_created_tenant(session: AsyncSession) -> None:
    created = await service.create_tenant(session, _sample_create())

    fetched = await service.get_tenant(session, created.id)

    assert fetched.id == created.id
    assert fetched.company_name == "Gamoft"


async def test_get_tenant_missing_raises_not_found(session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await service.get_tenant(session, uuid4())


async def test_activate_tenant_sets_active_and_activated_at(session: AsyncSession) -> None:
    created = await service.create_tenant(session, _sample_create())

    activated = await service.activate_tenant(session, created.id)

    assert activated.status is TenantStatus.ACTIVE
    assert activated.activated_at is not None


async def test_activate_tenant_twice_raises_conflict(session: AsyncSession) -> None:
    created = await service.create_tenant(session, _sample_create())
    await service.activate_tenant(session, created.id)

    with pytest.raises(ConflictError):
        await service.activate_tenant(session, created.id)


async def test_is_active_reflects_status(session: AsyncSession) -> None:
    created = await service.create_tenant(session, _sample_create())
    assert service.is_active(created) is False

    activated = await service.activate_tenant(session, created.id)
    assert service.is_active(activated) is True
