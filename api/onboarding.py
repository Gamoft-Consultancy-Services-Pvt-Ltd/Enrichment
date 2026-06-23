"""The /onboarding endpoint — verify PAN (KYB), create the tenant, start pipeline."""

from typing import Annotated

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import get_current_user
from auth.models import User
from auth.service import set_user_tenant
from core.db import get_session
from core.exceptions import ConflictError, UnprocessableError
from core.queue import get_arq_pool
from modules.tenant_onboarding.kyb import verify_pan_kyb
from shared.tenant.schemas import OnboardingStatus, TenantCreate, TenantRead
from shared.tenant.service import create_tenant, get_tenant

router = APIRouter()


@router.post("/onboarding", response_model=TenantRead)
async def onboard(
    data: TenantCreate,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
) -> TenantRead:
    """Verify the PAN, then (only on success) create the tenant and start the pipeline.

    Idempotent recovery: if the user already has a tenant whose pipeline never started
    (onboarding_status PENDING — the enqueue-failure case), re-enqueue and return it
    instead of 409. Any other status is a genuine repeat onboarding -> 409.
    """
    if user.tenant_id is not None:
        existing = await get_tenant(session, user.tenant_id)  # raises NotFoundError if gone
        # onboarding_status is a String column -> compare with == (a plain str), not is.
        if existing.onboarding_status == OnboardingStatus.PENDING:
            await arq_pool.enqueue_job("run_onboarding_pipeline", tenant_id=str(existing.id))
            return TenantRead.model_validate(existing)
        raise ConflictError("User is already onboarded to a tenant")

    # Verify-then-create: a failed match creates nothing, so there are no orphan rows.
    company_data = await verify_pan_kyb(data.pan, data.pan_holder_name, data.pan_dob)
    if company_data is None:
        raise UnprocessableError("PAN could not be verified")

    tenant = await create_tenant(session, data, kyb_company_data=company_data)
    await set_user_tenant(session, user, tenant.id)
    # If enqueue fails here the tenant is VERIFIED but PENDING; the user can simply retry
    # /onboarding and the idempotent-recovery branch above will re-enqueue it.
    await arq_pool.enqueue_job("run_onboarding_pipeline", tenant_id=str(tenant.id))
    return TenantRead.model_validate(tenant)
