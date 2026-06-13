"""Lead ingestion API endpoints.

Sprint 2: POST /channels/inbound/file-upload
Sprint 3: GET/POST /channels/webhook  (Meta webhook verify + receive)
"""

import json
from typing import Annotated, Any

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import get_current_user
from auth.models import User
from core.config import get_settings
from core.db import get_session
from core.queue import get_arq_pool
from modules.lead_ingestion.service import (
    HmacValidationError,
    get_whatsapp_connection_by_phone_number_id,
    handle_file_upload,
    validate_signature,
)

router = APIRouter()


@router.get("/webhook")
async def verify_webhook(
    hub_mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    hub_verify_token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    hub_challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> Response:
    """Meta webhook verification handshake (hub challenge).

    Meta sends a GET with hub.mode=subscribe, hub.verify_token, and hub.challenge.
    We verify the token and echo the challenge back as plain text.
    """
    settings = get_settings()
    if (
        hub_mode == "subscribe"
        and hub_verify_token == settings.meta_webhook_verify_token
        and hub_challenge is not None
    ):
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="webhook_verify_failed")


@router.post("/webhook")
async def receive_webhook(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
) -> dict[str, str]:
    """Receive a Meta webhook event, validate its HMAC signature, and enqueue processing."""
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    settings = get_settings()
    try:
        validate_signature(raw_body, signature, app_secret=settings.meta_app_secret)
    except HmacValidationError as exc:
        raise HTTPException(status_code=403, detail="invalid_signature") from exc

    try:
        payload: dict[str, Any] = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="invalid_json") from exc

    # Extract phone_number_id to identify which tenant this event belongs to
    try:
        phone_number_id: str = payload["entry"][0]["changes"][0]["value"]["metadata"][
            "phone_number_id"
        ]
    except (KeyError, IndexError):
        # Malformed or non-message event (e.g. status updates) — ack and discard
        return {"status": "ignored"}

    connection = await get_whatsapp_connection_by_phone_number_id(session, phone_number_id)
    if connection is None:
        # No registered tenant for this phone number — ack to Meta but don't process
        return {"status": "unknown_connection"}

    await arq_pool.enqueue_job(
        "run_lead_capture",
        {
            "tenant_id": str(connection.tenant_id),
            "channel_connection_id": str(connection.id),
            "raw_payload": payload,
        },
    )
    return {"status": "received"}


@router.post("/inbound/file-upload")
async def upload_leads(
    file: UploadFile,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
) -> dict[str, Any]:
    """Accept a CSV or XLSX file and ingest its rows as leads for the current tenant."""
    if user.tenant_id is None:
        raise HTTPException(status_code=400, detail="User has no associated tenant")

    file_bytes = await file.read()
    return await handle_file_upload(
        file_bytes=file_bytes,
        filename=file.filename or "upload",
        tenant_id=user.tenant_id,
        session=session,
        arq_pool=arq_pool,
    )
