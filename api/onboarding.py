"""The /onboarding endpoint — verify PAN (KYB), create the tenant, start pipeline."""

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
from core.exceptions import ConflictError, UnprocessableError
from core.queue import get_arq_pool
from modules.tenant_onboarding.kyb import verify_pan_kyb
from shared.tenant.schemas import OnboardingStatus, TenantCreate, TenantRead
from shared.tenant.service import create_tenant, get_tenant

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
    """Verify the PAN, then (only on success) create the tenant and start the pipeline.

    Idempotent recovery: if the user already has a tenant whose pipeline never started
    (onboarding_status PENDING — the enqueue-failure case), re-enqueue and return it
    instead of 409. Any other status is a genuine repeat onboarding -> 409.
    """
    if user.tenant_id is not None:
        existing = await get_tenant(session, user.tenant_id)  # raises NotFoundError if gone
        # onboarding_status is a String column -> compare with == (a plain str), not is.
        if existing.onboarding_status == OnboardingStatus.PENDING:
            # Deterministic job id: arq drops a duplicate enqueue for the same tenant,
            # so a stale PENDING read can't start a second onboarding run.
            await arq_pool.enqueue_job(
                "run_onboarding_pipeline",
                tenant_id=str(existing.id),
                _job_id=f"onboarding:{existing.id}",
            )
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
    await arq_pool.enqueue_job(
        "run_onboarding_pipeline",
        tenant_id=str(tenant.id),
        _job_id=f"onboarding:{tenant.id}",
    )
    try:
        await _update_auth0_user_metadata(user.auth0_sub, str(tenant.id))
    except Exception:
        log.exception("auth0_metadata_update_failed", auth0_sub=user.auth0_sub)
    return TenantRead.model_validate(tenant)
