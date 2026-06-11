"""Pre-flight check: verify the tenant has an active config with at least one signal."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from modules.lead_ingestion.exceptions import PreFlightHaltError
from shared.tenant_config.schemas import TenantConfigRead
from shared.tenant_config.service import get_active_config


async def check_pre_flight(session: AsyncSession, tenant_id: UUID) -> TenantConfigRead:
    """Return the active config, or raise PreFlightHaltError.

    Raises:
        PreFlightHaltError: if no ACTIVE config exists or its signals list is empty.
    """
    config = await get_active_config(session, tenant_id)
    if config is None:
        raise PreFlightHaltError("no_active_config")
    if not config.signals:
        raise PreFlightHaltError("empty_signals")
    return config
