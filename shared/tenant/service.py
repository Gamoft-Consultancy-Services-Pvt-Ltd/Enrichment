"""Public service for tenants — the only path to create, read, and activate them."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import ConflictError, NotFoundError
from shared.tenant.models import Tenant
from shared.tenant.schemas import KybStatus, OnboardingStatus, TenantCreate, TenantStatus


async def create_tenant(
    session: AsyncSession,
    data: TenantCreate,
    *,
    kyb_company_data: dict[str, Any] | None = None,
) -> Tenant:
    """Create a tenant in the CREATED state and return it.

    When kyb_company_data is provided (the onboarding verify-then-create path),
    the tenant is born KYB-VERIFIED; otherwise kyb_status defaults to PENDING.
    """
    tenant = Tenant(
        company_name=data.company_name,
        primary_contact_name=data.primary_contact_name,
        primary_contact_email=data.primary_contact_email,
        business_type=data.business_type,
        website_url=str(data.website_url),
        pan=data.pan,
        timezone=data.timezone,
        language_preference=data.language_preference,
        status=TenantStatus.CREATED,
        onboarding_status=OnboardingStatus.PENDING,
    )
    if kyb_company_data is not None:
        tenant.kyb_status = KybStatus.VERIFIED
        tenant.kyb_company_data = kyb_company_data
        tenant.kyb_verified_at = datetime.now(UTC)
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
