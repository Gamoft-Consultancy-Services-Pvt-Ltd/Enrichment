"""KYB orchestration: GST-OTP send/verify/resend/restart for tenant onboarding.

Composes the Surepass client (network) with tenant service (DB state). Owns the
business rules — attempt and resend caps, and what counts as pass/fail. Does NOT
enqueue the pipeline; the api layer does that on a VERIFIED result.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from clients import surepass_client
from core.exceptions import ConflictError
from shared.tenant import service as tenant_service
from shared.tenant.schemas import KybStatus

MAX_ATTEMPTS = 3
MAX_RESENDS = 3


async def start_verification(session: AsyncSession, tenant_id: UUID) -> None:
    """Send the first OTP for a freshly created (PENDING) tenant."""
    tenant = await tenant_service.get_tenant(session, tenant_id)
    txn_ref = await surepass_client.send_gst_otp(tenant.gstin)
    await tenant_service.store_kyb_txn(session, tenant_id, txn_ref)


async def submit_otp(session: AsyncSession, tenant_id: UUID, otp: str) -> KybStatus:
    """Verify an OTP. Returns the resulting KybStatus.

    VERIFIED on success; PENDING while attempts remain; FAILED at the cap.
    """
    tenant = await tenant_service.get_tenant(session, tenant_id)
    if tenant.kyb_status != KybStatus.PENDING:
        raise ConflictError(f"Tenant {tenant_id} KYB is not pending")
    if tenant.kyb_txn_ref is None:
        raise ConflictError(f"Tenant {tenant_id} has no active OTP")

    company = await surepass_client.verify_gst_otp(tenant.kyb_txn_ref, otp)
    if company is not None:
        await tenant_service.mark_kyb_verified(session, tenant_id, dict(company))
        return KybStatus.VERIFIED

    attempts = await tenant_service.bump_kyb_attempts(session, tenant_id)
    if attempts >= MAX_ATTEMPTS:
        await tenant_service.mark_kyb_failed(session, tenant_id)
        return KybStatus.FAILED
    return KybStatus.PENDING


async def resend_otp(session: AsyncSession, tenant_id: UUID) -> None:
    """Send a fresh OTP for the same GSTIN, capped at MAX_RESENDS."""
    tenant = await tenant_service.get_tenant(session, tenant_id)
    if tenant.kyb_status != KybStatus.PENDING:
        raise ConflictError(f"Tenant {tenant_id} KYB is not pending")
    if tenant.kyb_resends >= MAX_RESENDS:
        raise ConflictError(f"Tenant {tenant_id} OTP resend limit reached")
    txn_ref = await surepass_client.send_gst_otp(tenant.gstin)
    await tenant_service.store_kyb_txn(session, tenant_id, txn_ref)
    await tenant_service.bump_kyb_resends(session, tenant_id)


async def restart_kyb(
    session: AsyncSession, tenant_id: UUID, gstin: str | None
) -> None:
    """Restart KYB for a FAILED tenant: reset counters, optional new GSTIN, fresh OTP."""
    tenant = await tenant_service.get_tenant(session, tenant_id)
    if tenant.kyb_status != KybStatus.FAILED:
        raise ConflictError(f"Tenant {tenant_id} KYB is not failed")
    await tenant_service.reset_kyb(session, tenant_id, gstin)
    refreshed = await tenant_service.get_tenant(session, tenant_id)
    txn_ref = await surepass_client.send_gst_otp(refreshed.gstin)
    await tenant_service.store_kyb_txn(session, tenant_id, txn_ref)
