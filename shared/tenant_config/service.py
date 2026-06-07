"""Public service for the tenant_config registry — the only path to read,
create, approve, reject, and list per-tenant scoring-config versions.

Holds the invariants scoring depends on: monotonic version numbering and the
atomic activation flip (at most one ACTIVE version per tenant). Policy (what to
build and when to approve) lives in modules/tenant_onboarding.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import ConflictError, NotFoundError
from shared.tenant_config.models import TenantConfig
from shared.tenant_config.schemas import ConfigStatus, TenantConfigCreate, TenantConfigRead


async def get_active_config(session: AsyncSession, tenant_id: UUID) -> TenantConfigRead | None:
    """Return the tenant's single ACTIVE version, or None if it has none."""
    result = await session.execute(
        select(TenantConfig).where(
            TenantConfig.tenant_id == tenant_id,
            TenantConfig.status == ConfigStatus.ACTIVE,
        )
    )
    config = result.scalar_one_or_none()
    return TenantConfigRead.model_validate(config) if config is not None else None


async def create_draft(
    session: AsyncSession, tenant_id: UUID, data: TenantConfigCreate
) -> TenantConfigRead:
    """Write a new DRAFT version for the tenant.

    Raises ConflictError if the tenant already has a DRAFT (one-draft rule).
    """
    existing = await session.execute(
        select(TenantConfig.id).where(
            TenantConfig.tenant_id == tenant_id,
            TenantConfig.status == ConfigStatus.DRAFT,
        )
    )
    if existing.first() is not None:
        raise ConflictError(f"Tenant {tenant_id} already has a draft config")

    config = TenantConfig(
        tenant_id=tenant_id,
        version=await _next_version(session, tenant_id),
        status=ConfigStatus.DRAFT,
        business_profile=data.business_profile,
        icp=data.icp,
        signals=[s.model_dump() for s in data.signals],
        weights=data.weights.model_dump(),
        thresholds=data.thresholds.model_dump(),
    )
    session.add(config)
    await session.commit()
    await session.refresh(config)
    return TenantConfigRead.model_validate(config)


async def approve_version(session: AsyncSession, config_id: UUID) -> TenantConfigRead:
    """Promote a DRAFT (or roll back to an ARCHIVED) version to ACTIVE.

    Any current ACTIVE version for the same tenant is archived in the same
    transaction. The previous ACTIVE is flushed to ARCHIVED *before* the new row
    is set ACTIVE, so the `uq_active_config_per_tenant` partial unique index is
    never momentarily violated. Raises ConflictError if the target is already
    ACTIVE or is REJECTED; NotFoundError if it does not exist.
    """
    config = await session.get(TenantConfig, config_id)
    if config is None:
        raise NotFoundError(f"Config {config_id} not found")
    if config.status is ConfigStatus.ACTIVE:
        raise ConflictError(f"Config {config_id} is already active")
    if config.status is ConfigStatus.REJECTED:
        raise ConflictError(f"Config {config_id} is rejected and cannot be activated")

    now = datetime.now(UTC)
    current = await session.execute(
        select(TenantConfig).where(
            TenantConfig.tenant_id == config.tenant_id,
            TenantConfig.status == ConfigStatus.ACTIVE,
        )
    )
    active = current.scalar_one_or_none()
    if active is not None:
        active.status = ConfigStatus.ARCHIVED
        active.archived_at = now
        await session.flush()  # archive the old ACTIVE before the new one is set ACTIVE

    config.status = ConfigStatus.ACTIVE
    config.activated_at = now
    config.archived_at = None  # clear if this is a rollback of a previously archived version
    await session.commit()
    await session.refresh(config)
    return TenantConfigRead.model_validate(config)


async def _next_version(session: AsyncSession, tenant_id: UUID) -> int:
    """The next monotonic version number for a tenant (1 if none yet)."""
    result = await session.execute(
        select(func.max(TenantConfig.version)).where(TenantConfig.tenant_id == tenant_id)
    )
    return (result.scalar_one_or_none() or 0) + 1
