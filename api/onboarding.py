"""The /onboarding endpoint — submits business info, creates tenant, starts pipeline."""

from typing import Annotated

import httpx
import structlog
from arq.connections import ArqRedis
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import get_current_user
from auth.models import User
from auth.service import set_user_tenant
from core.config import get_settings
from core.db import get_session
from core.exceptions import ConflictError
from core.queue import get_arq_pool
from shared.tenant.schemas import TenantCreate, TenantRead
from shared.tenant.service import create_tenant

router = APIRouter()
log = structlog.get_logger(__name__)


async def _update_auth0_user_metadata(auth0_sub: str, tenant_id: str) -> None:
    """Set role=TENANT and tenant_id on the Auth0 user so their next JWT carries
    the correct claims. Failure is logged but does not block the onboarding response
    — the user can still use the app after their next login (Auth0 refreshes the token)."""
    settings = get_settings()
    if not settings.auth0_mgmt_client_id or not settings.auth0_mgmt_client_secret:
        log.warning("auth0_mgmt_not_configured", auth0_sub=auth0_sub)
        return
    base = f"https://{settings.auth0_domain}"
    async with httpx.AsyncClient() as client:
        # 1. Get a Management API token
        token_resp = await client.post(
            f"{base}/oauth/token",
            json={
                "grant_type": "client_credentials",
                "client_id": settings.auth0_mgmt_client_id,
                "client_secret": settings.auth0_mgmt_client_secret,
                "audience": f"{base}/api/v2/",
            },
            timeout=10.0,
        )
        token_resp.raise_for_status()
        mgmt_token = token_resp.json()["access_token"]

        # 2. Patch the user's app_metadata
        patch_resp = await client.patch(
            f"{base}/api/v2/users/{auth0_sub}",
            headers={"Authorization": f"Bearer {mgmt_token}"},
            json={"app_metadata": {"role": "TENANT", "tenant_id": tenant_id}},
            timeout=10.0,
        )
        patch_resp.raise_for_status()
    log.info("auth0_metadata_updated", auth0_sub=auth0_sub, tenant_id=tenant_id)


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
    try:
        await _update_auth0_user_metadata(user.auth0_sub, str(tenant.id))
    except Exception:
        log.exception("auth0_metadata_update_failed", auth0_sub=user.auth0_sub)
    return TenantRead.model_validate(tenant)
