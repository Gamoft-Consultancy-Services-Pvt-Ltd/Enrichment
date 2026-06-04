"""The /onboarding endpoint — the current user submits business info to create their tenant."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import get_current_user
from auth.models import User
from auth.service import set_user_tenant
from core.db import get_session
from core.exceptions import ConflictError
from shared.tenant.schemas import TenantCreate, TenantRead
from shared.tenant.service import create_tenant

router = APIRouter()


@router.post("/onboarding", response_model=TenantRead)
async def onboard(
    data: TenantCreate,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TenantRead:
    """Create the current user's tenant from their business info, then link it."""
    if user.tenant_id is not None:
        raise ConflictError("User is already onboarded to a tenant")
    # Two commits (create_tenant, then set_user_tenant); a crash between them can
    # leave an orphan Tenant. Accepted for this slice — don't widen this window by
    # adding work between the calls. A future onboarding module wraps both in one tx.
    tenant = await create_tenant(session, data)
    await set_user_tenant(session, user, tenant.id)
    return TenantRead.model_validate(tenant)
