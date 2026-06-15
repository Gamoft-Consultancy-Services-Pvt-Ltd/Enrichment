"""The /onboarding endpoints — create tenant, run GST-OTP KYB, then start pipeline."""

from typing import Annotated
from uuid import UUID

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import get_current_user
from auth.models import User
from auth.service import set_user_tenant
from core.db import get_session
from core.exceptions import ConflictError
from core.queue import get_arq_pool
from modules.tenant_onboarding import kyb
from shared.tenant.schemas import KybStatus, TenantCreate, TenantRead, normalize_gstin
from shared.tenant.service import create_tenant, get_tenant

router = APIRouter()


class OtpVerifyRequest(BaseModel):
    otp: str


class RestartKybRequest(BaseModel):
    gstin: str | None = None

    @field_validator("gstin")
    @classmethod
    def _normalize(cls, value: str | None) -> str | None:
        return None if value is None else normalize_gstin(value)


@router.post("/onboarding", response_model=TenantRead)
async def onboard(
    data: TenantCreate,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TenantRead:
    """Create the tenant in KYB_PENDING and send the first GST OTP. No pipeline yet."""
    if user.tenant_id is not None:
        raise ConflictError("User is already onboarded to a tenant")
    # Three sequential commits (create_tenant → set_user_tenant → start_verification).
    # A crash after set_user_tenant but before start_verification leaves the tenant
    # PENDING with no kyb_txn_ref; recovery is POST /onboarding/resend-otp (which only
    # requires PENDING status, not a txn_ref). Accepted for this slice.
    tenant = await create_tenant(session, data)
    await set_user_tenant(session, user, tenant.id)
    await kyb.start_verification(session, tenant.id)
    # kyb committed an UPDATE, expiring server-computed columns (updated_at); refresh
    # repopulates them so the response can be serialized without an async lazy-load.
    refreshed = await get_tenant(session, tenant.id)
    await session.refresh(refreshed)
    return TenantRead.model_validate(refreshed)


@router.post("/onboarding/verify-otp", response_model=TenantRead)
async def verify_otp(
    body: OtpVerifyRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
) -> TenantRead:
    """Verify the OTP; on success, enqueue the onboarding pipeline."""
    tenant_id = _require_tenant(user)
    result = await kyb.submit_otp(session, tenant_id, body.otp)
    if result == KybStatus.VERIFIED:
        # KYB is already committed VERIFIED. If enqueue fails here (e.g. Redis down),
        # the tenant is VERIFIED but the pipeline never starts, and the KYB endpoints
        # can't self-recover it (not PENDING, not FAILED) — needs admin reconciliation.
        # A startup VERIFIED-without-pipeline sweep is a deferred follow-up.
        await arq_pool.enqueue_job("run_onboarding_pipeline", tenant_id=str(tenant_id))
    refreshed = await get_tenant(session, tenant_id)
    await session.refresh(refreshed)
    return TenantRead.model_validate(refreshed)


@router.post("/onboarding/resend-otp", response_model=TenantRead)
async def resend_otp(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TenantRead:
    """Send a fresh OTP for the same GSTIN (capped)."""
    tenant_id = _require_tenant(user)
    await kyb.resend_otp(session, tenant_id)
    refreshed = await get_tenant(session, tenant_id)
    await session.refresh(refreshed)
    return TenantRead.model_validate(refreshed)


@router.post("/onboarding/restart-kyb", response_model=TenantRead)
async def restart_kyb(
    body: RestartKybRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TenantRead:
    """Restart KYB for a FAILED tenant, optionally with a corrected GSTIN."""
    tenant_id = _require_tenant(user)
    await kyb.restart_kyb(session, tenant_id, body.gstin)
    refreshed = await get_tenant(session, tenant_id)
    await session.refresh(refreshed)
    return TenantRead.model_validate(refreshed)


def _require_tenant(user: User) -> UUID:
    if user.tenant_id is None:
        raise ConflictError("User has no tenant to verify")
    return user.tenant_id
