"""Public service for the tenant_config registry.

The pipeline writes configs directly as ACTIVE (no draft/approval step).
Re-running onboarding archives the previous ACTIVE and creates a new one.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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


async def create_active(
    session: AsyncSession, tenant_id: UUID, data: TenantConfigCreate
) -> TenantConfigRead:
    """Write a new ACTIVE config, archiving any previous ACTIVE version.

    The archive + insert are flushed before committing so the
    uq_active_config_per_tenant partial index is never momentarily violated.
    """
    now = datetime.now(UTC)
    existing = await session.execute(
        select(TenantConfig).where(
            TenantConfig.tenant_id == tenant_id,
            TenantConfig.status == ConfigStatus.ACTIVE,
        )
    )
    current_active = existing.scalar_one_or_none()
    if current_active is not None:
        current_active.status = ConfigStatus.ARCHIVED
        current_active.archived_at = now
        await session.flush()

    config = TenantConfig(
        tenant_id=tenant_id,
        version=await _next_version(session, tenant_id),
        status=ConfigStatus.ACTIVE,
        activated_at=now,
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


async def list_versions(session: AsyncSession, tenant_id: UUID) -> list[TenantConfigRead]:
    """Return all versions for a tenant, newest first."""
    result = await session.execute(
        select(TenantConfig)
        .where(TenantConfig.tenant_id == tenant_id)
        .order_by(TenantConfig.version.desc())
    )
    return [TenantConfigRead.model_validate(c) for c in result.scalars().all()]


async def _next_version(session: AsyncSession, tenant_id: UUID) -> int:
    """The next monotonic version number for a tenant (1 if none yet)."""
    result = await session.execute(
        select(func.max(TenantConfig.version)).where(TenantConfig.tenant_id == tenant_id)
    )
    return (result.scalar_one_or_none() or 0) + 1
