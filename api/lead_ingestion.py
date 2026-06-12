"""Lead ingestion API endpoints.

Sprint 2: POST /channels/inbound/file-upload only.
Sprint 6 will add the full channel router at this prefix.
"""

from typing import Annotated, Any

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import get_current_user
from auth.models import User
from core.db import get_session
from core.queue import get_arq_pool
from modules.lead_ingestion.file_upload_handler import handle_file_upload

router = APIRouter()


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
