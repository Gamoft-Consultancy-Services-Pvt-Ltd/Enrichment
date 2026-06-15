"""Public service for tenants — the only path to create, read, and activate them."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import ConflictError, NotFoundError
from shared.tenant.models import Tenant
from shared.tenant.schemas import KybStatus, OnboardingStatus, TenantCreate, TenantStatus


async def create_tenant(session: AsyncSession, data: TenantCreate) -> Tenant:
    """Create a tenant in the CREATED state and return it."""
    tenant = Tenant(
        company_name=data.company_name,
        primary_contact_name=data.primary_contact_name,
        primary_contact_email=data.primary_contact_email,
        business_type=data.business_type,
        website_url=str(data.website_url),
        gstin=data.gstin,
        kyb_status=KybStatus.PENDING,
        timezone=data.timezone,
        language_preference=data.language_preference,
        status=TenantStatus.CREATED,
        onboarding_status=OnboardingStatus.PENDING,
    )
    session.add(tenant)
    await session.commit()
    await session.refresh(tenant)
    return tenant


async def get_tenant(session: AsyncSession, tenant_id: UUID) -> Tenant:
    """Return the tenant or raise NotFoundError."""
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFoundError(f"Tenant {tenant_id} not found")
    return tenant


async def activate_tenant(session: AsyncSession, tenant_id: UUID) -> Tenant:
    """Move a CREATED tenant to ACTIVE. Raise ConflictError if not CREATED."""
    tenant = await get_tenant(session, tenant_id)
    if tenant.status is not TenantStatus.CREATED:
        raise ConflictError(
            f"Tenant {tenant_id} cannot be activated from status {tenant.status.value}"
        )
    tenant.status = TenantStatus.ACTIVE
    tenant.activated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(tenant)
    return tenant


async def set_onboarding_status(
    session: AsyncSession, tenant_id: UUID, status: OnboardingStatus
) -> None:
    """Update the onboarding_status on the tenant row. No-op if tenant not found."""
    tenant = await session.get(Tenant, tenant_id)
    if tenant is not None:
        tenant.onboarding_status = status
        await session.commit()


def is_active(tenant: Tenant) -> bool:
    """The activation gate other layers consult."""
    return tenant.status is TenantStatus.ACTIVE


async def store_kyb_txn(session: AsyncSession, tenant_id: UUID, txn_ref: str) -> None:
    """Persist the Surepass transaction reference for the pending OTP."""
    tenant = await get_tenant(session, tenant_id)
    tenant.kyb_txn_ref = txn_ref
    await session.commit()


async def mark_kyb_verified(
    session: AsyncSession, tenant_id: UUID, company_data: dict[str, Any]
) -> None:
    """Record a successful KYB: store company data, set VERIFIED, clear the txn ref."""
    tenant = await get_tenant(session, tenant_id)
    tenant.kyb_status = KybStatus.VERIFIED
    tenant.kyb_company_data = company_data
    tenant.kyb_verified_at = datetime.now(UTC)
    tenant.kyb_txn_ref = None
    await session.commit()


async def bump_kyb_attempts(session: AsyncSession, tenant_id: UUID) -> int:
    """Increment the wrong-OTP attempt counter and return the new value."""
    tenant = await get_tenant(session, tenant_id)
    tenant.kyb_attempts += 1
    await session.commit()
    return tenant.kyb_attempts


async def bump_kyb_resends(session: AsyncSession, tenant_id: UUID) -> int:
    """Increment the OTP resend counter and return the new value."""
    tenant = await get_tenant(session, tenant_id)
    tenant.kyb_resends += 1
    await session.commit()
    return tenant.kyb_resends


async def mark_kyb_failed(session: AsyncSession, tenant_id: UUID) -> None:
    """Set KYB to FAILED and clear the txn ref."""
    tenant = await get_tenant(session, tenant_id)
    tenant.kyb_status = KybStatus.FAILED
    tenant.kyb_txn_ref = None
    await session.commit()


async def reset_kyb(
    session: AsyncSession, tenant_id: UUID, gstin: str | None = None
) -> None:
    """Reset a FAILED tenant to PENDING, clear counters, optionally swap the GSTIN."""
    tenant = await get_tenant(session, tenant_id)
    tenant.kyb_status = KybStatus.PENDING
    tenant.kyb_attempts = 0
    tenant.kyb_resends = 0
    tenant.kyb_txn_ref = None
    if gstin is not None:
        tenant.gstin = gstin
    await session.commit()
