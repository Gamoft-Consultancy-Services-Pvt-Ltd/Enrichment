"""File upload handler: parse CSV/XLSX, validate, run rows through the pipeline.

Pre-flight is checked once before the row loop — if the tenant has no active
config the whole upload is rejected (no partial leads are written).
Rows with no identity fields (no name, phone, or email) are written as
pre_flight_blocked leads and never discarded.
"""

import csv
import io
from pathlib import Path
from typing import Any
from uuid import UUID

import openpyxl
import structlog
from arq.connections import ArqRedis
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from modules.lead_ingestion.data_policy import strip_sensitive_fields
from modules.lead_ingestion.db.models import Lead
from modules.lead_ingestion.exceptions import PreFlightHaltError
from modules.lead_ingestion.normaliser import normalise_file_row
from modules.lead_ingestion.pipeline import run_capture
from modules.lead_ingestion.pre_flight import check_pre_flight
from shared.events.schemas import LeadSource

log = structlog.get_logger(__name__)

_MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
_MAX_ROWS = 5_000
_SYNC_THRESHOLD = 100
_ALLOWED_SUFFIXES = frozenset({".csv", ".xlsx"})


def _parse_csv(data: bytes) -> list[dict[str, str]]:
    text = data.decode("utf-8-sig")  # strip BOM if present
    reader = csv.DictReader(io.StringIO(text))
    return [dict(row) for row in reader]


def _parse_xlsx(data: bytes) -> list[dict[str, Any]]:
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.active
    if ws is None:
        return []
    all_rows = list(ws.iter_rows(values_only=True))
    if not all_rows:
        return []
    headers = [str(h) if h is not None else "" for h in all_rows[0]]
    return [dict(zip(headers, row, strict=False)) for row in all_rows[1:]]


def _stringify_row(row: dict[str, Any]) -> dict[str, str]:
    return {k: str(v) if v is not None else "" for k, v in row.items()}


async def _write_blocked_lead(
    session: AsyncSession,
    tenant_id: UUID,
    row: dict[str, str],
    extra_fields: dict[str, Any],
    reason: str,
) -> Lead:
    lead = Lead(
        tenant_id=tenant_id,
        pipeline_stage="pre_flight_blocked",
        source_channel=LeadSource.FILE_UPLOAD.value,
        pre_flight_block_reason=reason,
        raw_event_json=row,
        extra_fields=extra_fields if extra_fields else None,
    )
    session.add(lead)
    await session.commit()
    return lead


async def handle_file_upload(
    file_bytes: bytes,
    filename: str,
    tenant_id: UUID,
    session: AsyncSession,
    arq_pool: ArqRedis,
) -> dict[str, Any]:
    """Validate, parse, and ingest a lead CSV/XLSX file.

    ≤ 100 rows are processed synchronously and their lead IDs returned.
    > 100 rows are enqueued as an ARQ batch job.
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported file type '{suffix}'. Accepted: .csv, .xlsx",
        )

    if len(file_bytes) > _MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File exceeds 10 MB limit")

    raw_rows: list[dict[str, Any]] = (
        _parse_csv(file_bytes) if suffix == ".csv" else _parse_xlsx(file_bytes)
    )

    if len(raw_rows) > _MAX_ROWS:
        raise HTTPException(
            status_code=422,
            detail=f"File has {len(raw_rows)} data rows; maximum is {_MAX_ROWS}",
        )

    try:
        await check_pre_flight(session, tenant_id)
    except PreFlightHaltError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    str_rows = [_stringify_row(r) for r in raw_rows]

    if len(str_rows) > _SYNC_THRESHOLD:
        await arq_pool.enqueue_job(
            "run_lead_capture_batch",
            payload_dict={"tenant_id": str(tenant_id), "rows": str_rows},
        )
        return {"mode": "async", "row_count": len(str_rows)}

    lead_ids: list[str] = []
    for i, row in enumerate(str_rows):
        event = normalise_file_row(row, tenant_id=tenant_id, row_index=i)
        if event.extra_fields:
            clean_extra, stripped = strip_sensitive_fields(event.extra_fields)
            if stripped:
                log.info(
                    "sensitive_fields_stripped",
                    tenant_id=str(tenant_id),
                    stripped_keys=sorted(stripped.keys()),
                )
                event = event.model_copy(update={"extra_fields": clean_extra})
        if not event.phone and not event.email and not event.full_name:
            blocked = await _write_blocked_lead(
                session, tenant_id, row, event.extra_fields, "insufficient_identity_fields"
            )
            lead_ids.append(str(blocked.id))
            continue
        lead, _ = await run_capture(session, event)
        lead_ids.append(str(lead.id))

    return {"mode": "sync", "lead_ids": lead_ids, "row_count": len(str_rows)}
