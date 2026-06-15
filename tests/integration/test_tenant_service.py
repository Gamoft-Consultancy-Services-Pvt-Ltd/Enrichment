"""Integration tests for shared.tenant.service against a real Postgres."""

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import ConflictError, NotFoundError
from shared.tenant import service
from shared.tenant.schemas import BusinessType, KybStatus, TenantCreate, TenantStatus
from shared.tenant.service import (
    bump_kyb_attempts,
    bump_kyb_resends,
    create_tenant,
    get_tenant,
    mark_kyb_failed,
    mark_kyb_verified,
    reset_kyb,
    store_kyb_txn,
)

_VALID_GSTIN = "29ABCDE1234F1Z5"


def _sample_create() -> TenantCreate:
    return TenantCreate(
        company_name="Gamoft",
        primary_contact_name="Asha",
        primary_contact_email="asha@gamoft.com",
        business_type=BusinessType.B2B,
        website_url="https://gamoft.com",  # type: ignore[arg-type]
        gstin=_VALID_GSTIN,
    )


def _tenant_create(gstin: str = _VALID_GSTIN) -> TenantCreate:
    return TenantCreate(
        company_name="Acme",
        primary_contact_name="Ada",
        primary_contact_email="ada@acme.com",
        business_type=BusinessType.B2B,
        website_url="https://acme.com",  # type: ignore[arg-type]
        gstin=gstin,
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


async def test_create_tenant_persists_gstin_and_pending_kyb(session: AsyncSession) -> None:
    tenant = await create_tenant(session, _tenant_create())
    assert tenant.gstin == _VALID_GSTIN
    assert tenant.kyb_status == KybStatus.PENDING
    assert tenant.kyb_attempts == 0


async def test_store_kyb_txn(session: AsyncSession) -> None:
    tenant = await create_tenant(session, _tenant_create())
    await store_kyb_txn(session, tenant.id, "txn-1")
    refreshed = await get_tenant(session, tenant.id)
    assert refreshed.kyb_txn_ref == "txn-1"


async def test_mark_kyb_verified(session: AsyncSession) -> None:
    tenant = await create_tenant(session, _tenant_create())
    await store_kyb_txn(session, tenant.id, "txn-1")
    company = {"gstin": _VALID_GSTIN, "legal_name": "ACME PRIVATE LIMITED"}
    await mark_kyb_verified(session, tenant.id, company)
    refreshed = await get_tenant(session, tenant.id)
    assert refreshed.kyb_status == KybStatus.VERIFIED
    assert refreshed.kyb_company_data is not None
    assert refreshed.kyb_company_data["legal_name"] == "ACME PRIVATE LIMITED"
    assert refreshed.kyb_verified_at is not None
    assert refreshed.kyb_txn_ref is None


async def test_bump_kyb_attempts_returns_count(session: AsyncSession) -> None:
    tenant = await create_tenant(session, _tenant_create())
    assert await bump_kyb_attempts(session, tenant.id) == 1
    assert await bump_kyb_attempts(session, tenant.id) == 2


async def test_mark_kyb_failed_clears_txn(session: AsyncSession) -> None:
    tenant = await create_tenant(session, _tenant_create())
    await store_kyb_txn(session, tenant.id, "txn-1")
    await mark_kyb_failed(session, tenant.id)
    refreshed = await get_tenant(session, tenant.id)
    assert refreshed.kyb_status == KybStatus.FAILED
    assert refreshed.kyb_txn_ref is None


async def test_reset_kyb_clears_counters_and_sets_gstin(session: AsyncSession) -> None:
    tenant = await create_tenant(session, _tenant_create())
    await bump_kyb_attempts(session, tenant.id)
    assert await bump_kyb_resends(session, tenant.id) == 1
    await mark_kyb_failed(session, tenant.id)
    new_gstin = "27AAAAA0000A1Z5"
    await reset_kyb(session, tenant.id, new_gstin)
    refreshed = await get_tenant(session, tenant.id)
    assert refreshed.kyb_status == KybStatus.PENDING
    assert refreshed.kyb_attempts == 0
    assert refreshed.kyb_resends == 0
    assert refreshed.gstin == new_gstin
