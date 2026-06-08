"""The /onboarding endpoint — submits business info, creates tenant, starts pipeline."""

from typing import Annotated

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import get_current_user
from auth.models import User
from auth.service import set_user_tenant
from core.db import get_session
from core.exceptions import ConflictError
from core.queue import get_arq_pool
from shared.tenant.schemas import TenantCreate, TenantRead
from shared.tenant.service import create_tenant

router = APIRouter()


@router.post("/onboarding", response_model=TenantRead)
async def onboard(
    data: TenantCreate,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
) -> TenantRead:
    """Create the current user's tenant from their business info, then start the pipeline."""
    if user.tenant_id is not None:
        raise ConflictError("User is already onboarded to a tenant")
    # Two commits (create_tenant, then set_user_tenant); a crash between them can
    # leave an orphan Tenant. Accepted for this slice.
    tenant = await create_tenant(session, data)
    await set_user_tenant(session, user, tenant.id)
    await arq_pool.enqueue_job("run_onboarding_pipeline", tenant_id=str(tenant.id))
    return TenantRead.model_validate(tenant)
