"""Lead ingestion API endpoints.

Sprint 2: POST /channels/inbound/file-upload
Sprint 3: GET/POST /channels/webhook  (Meta webhook verify + receive)
Sprint 4: OAuth initiation + callbacks for Facebook, Instagram, WhatsApp Embedded Signup
"""

import json
from typing import Annotated, Any

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import get_current_user
from auth.models import User
from core.config import get_settings
from core.db import get_session
from core.queue import get_arq_pool
from modules.lead_ingestion.oauth.facebook import build_facebook_auth_url, exchange_facebook_code
from modules.lead_ingestion.oauth.instagram import (
    build_instagram_auth_url,
    exchange_instagram_code,
)
from modules.lead_ingestion.oauth.whatsapp import exchange_whatsapp_signup_code
from modules.lead_ingestion.service import (
    HmacValidationError,
    OAuthStateError,
    get_connection_by_page_or_ig_account_id,
    get_whatsapp_connection_by_phone_number_id,
    handle_file_upload,
    log_unroutable_event,
    validate_signature,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Meta webhook verification + receive
# ---------------------------------------------------------------------------


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
    """Receive a Meta webhook event, validate its HMAC signature, and enqueue processing.

    Routes by the 'object' field:
      - 'whatsapp_business_account' → WhatsApp messages (route by phone_number_id)
      - 'page'                      → Facebook DMs (route by page_id)
      - 'instagram'                 → Instagram DMs (route by ig_account_id)
    """
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

    object_type: str = payload.get("object", "")

    if object_type == "whatsapp_business_account":
        return await _route_whatsapp(payload, session, arq_pool)

    if object_type in ("page", "instagram"):
        return await _route_facebook_instagram(payload, session, arq_pool)

    # Unknown or unsupported object type — ack and discard
    return {"status": "ignored"}


async def _route_whatsapp(
    payload: dict[str, Any],
    session: AsyncSession,
    arq_pool: ArqRedis,
) -> dict[str, str]:
    """Route a WhatsApp webhook event by phone_number_id."""
    try:
        phone_number_id: str = payload["entry"][0]["changes"][0]["value"]["metadata"][
            "phone_number_id"
        ]
    except (KeyError, IndexError):
        return {"status": "ignored"}

    connection = await get_whatsapp_connection_by_phone_number_id(session, phone_number_id)
    if connection is None:
        try:
            platform_event_id: str = payload["entry"][0]["changes"][0]["value"]["messages"][0]["id"]
            await log_unroutable_event(session, platform_event_id, "WHATSAPP", payload)
        except (KeyError, IndexError):
            pass
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


async def _route_facebook_instagram(
    payload: dict[str, Any],
    session: AsyncSession,
    arq_pool: ArqRedis,
) -> dict[str, str]:
    """Route a Facebook (page) or Instagram webhook event by page_id / ig_account_id."""
    try:
        account_id: str = payload["entry"][0]["id"]
    except (KeyError, IndexError):
        return {"status": "ignored"}

    connection = await get_connection_by_page_or_ig_account_id(session, account_id)
    if connection is None:
        try:
            platform_event_id_fb: str = payload["entry"][0]["messaging"][0]["message"]["mid"]
            object_type: str = str(payload.get("object", ""))
            source_channel = "FACEBOOK" if object_type == "page" else "INSTAGRAM"
            await log_unroutable_event(session, platform_event_id_fb, source_channel, payload)
        except (KeyError, IndexError):
            pass
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


# ---------------------------------------------------------------------------
# File upload
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Facebook OAuth
# ---------------------------------------------------------------------------


@router.get("/oauth/facebook")
async def initiate_facebook_oauth(
    user: Annotated[User, Depends(get_current_user)],
) -> RedirectResponse:
    """Redirect the authenticated tenant user to Facebook's OAuth consent screen."""
    if user.tenant_id is None:
        raise HTTPException(status_code=400, detail="User has no associated tenant")
    settings = get_settings()
    url = build_facebook_auth_url(user.tenant_id, settings=settings)
    return RedirectResponse(url=url)


@router.get("/oauth/facebook/callback")
async def facebook_oauth_callback(
    session: Annotated[AsyncSession, Depends(get_session)],
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Handle the Facebook OAuth callback, exchange the code, and create connections."""
    if error or not code or not state:
        raise HTTPException(
            status_code=400,
            detail=f"Facebook OAuth error: {error or 'missing code or state'}",
        )
    settings = get_settings()
    try:
        connections = await exchange_facebook_code(code, state, session=session, settings=settings)
    except OAuthStateError as exc:
        raise HTTPException(status_code=403, detail="invalid_oauth_state") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Facebook connection failed: {exc}") from exc

    return {
        "status": "connected",
        "connections": [str(c.id) for c in connections],
        "page_count": len(connections),
    }


# ---------------------------------------------------------------------------
# Instagram OAuth
# ---------------------------------------------------------------------------


@router.get("/oauth/instagram")
async def initiate_instagram_oauth(
    user: Annotated[User, Depends(get_current_user)],
) -> RedirectResponse:
    """Redirect the authenticated tenant user to Instagram's OAuth consent screen."""
    if user.tenant_id is None:
        raise HTTPException(status_code=400, detail="User has no associated tenant")
    settings = get_settings()
    url = build_instagram_auth_url(user.tenant_id, settings=settings)
    return RedirectResponse(url=url)


@router.get("/oauth/instagram/callback")
async def instagram_oauth_callback(
    session: Annotated[AsyncSession, Depends(get_session)],
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Handle the Instagram OAuth callback, exchange the code, and create the connection."""
    if error or not code or not state:
        raise HTTPException(
            status_code=400,
            detail=f"Instagram OAuth error: {error or 'missing code or state'}",
        )
    settings = get_settings()
    try:
        conn = await exchange_instagram_code(code, state, session=session, settings=settings)
    except OAuthStateError as exc:
        raise HTTPException(status_code=403, detail="invalid_oauth_state") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Instagram connection failed: {exc}") from exc

    return {"status": "connected", "connection_id": str(conn.id)}


# ---------------------------------------------------------------------------
# WhatsApp Embedded Signup
# ---------------------------------------------------------------------------


class EmbeddedSignupRequest(BaseModel):
    code: str


@router.post("/embedded-signup/callback")
async def whatsapp_embedded_signup_callback(
    body: EmbeddedSignupRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Receive the Embedded Signup code from the frontend and provision WhatsApp connections."""
    if user.tenant_id is None:
        raise HTTPException(status_code=400, detail="User has no associated tenant")
    settings = get_settings()
    try:
        connections = await exchange_whatsapp_signup_code(
            body.code,
            session=session,
            settings=settings,
            tenant_id=user.tenant_id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"WhatsApp connection failed: {exc}"
        ) from exc

    return {
        "status": "connected",
        "connections": [str(c.id) for c in connections],
        "phone_count": len(connections),
    }
