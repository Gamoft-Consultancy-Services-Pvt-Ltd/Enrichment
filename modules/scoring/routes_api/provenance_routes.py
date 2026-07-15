"""
COMP-1201-ST7 — Provenance retrieval (Epic 6, Domain 12)

A read API over the provenance captured on each score. Given a tenant + lead,
return the typed ScoringProvenance for the latest (or every) stored score, so
auditors / the explainability layer (COMP-1202) / data-subject-rights flows
(Domain 4) can answer "what produced this decision?".

Two pieces:
- ProvenanceService: a thin read service over the repository that pulls the
  provenance back out of breakdown['provenance'] and rehydrates it into the
  typed model.
- router: FastAPI endpoints exposing it.

This file is additive — it does not change the scoring write path. Provenance is
already written by ScoringService; here we only read it back.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.repository import ScoringRepository
from schemas.confidence import ConfidenceCapture
from schemas.provenance import ScoringProvenance


class ProvenanceNotFound(Exception):
    def __init__(self, tenant_id: str, lead_id: str) -> None:
        super().__init__(f"No scored result for lead {lead_id!r} (tenant {tenant_id!r})")
        self.tenant_id = tenant_id
        self.lead_id = lead_id


class ProvenanceService:
    """Read-side service: rehydrate provenance from stored scores."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_latest(self, *, tenant_id: str, lead_id: str) -> ScoringProvenance:
        async with self._session_factory() as session:
            rec = await ScoringRepository(session).get_latest_result(
                tenant_id=tenant_id, lead_id=lead_id
            )
        if rec is None:
            raise ProvenanceNotFound(tenant_id, lead_id)
        return _extract(rec.breakdown, tenant_id, lead_id)

    async def get_history(
        self, *, tenant_id: str, lead_id: str
    ) -> list[ScoringProvenance]:
        async with self._session_factory() as session:
            records = await ScoringRepository(session).list_results_for_lead(
                tenant_id=tenant_id, lead_id=lead_id
            )
        if not records:
            raise ProvenanceNotFound(tenant_id, lead_id)
        out: list[ScoringProvenance] = []
        for rec in records:
            try:
                out.append(_extract(rec.breakdown, tenant_id, lead_id))
            except HTTPException:
                continue  # skip any legacy row without provenance
        return out

    async def get_confidence(self, *, tenant_id: str, lead_id: str) -> dict[str, Any]:
        """COMP-1203-ST4: return the traceable confidence capture for a score."""
        async with self._session_factory() as session:
            rec = await ScoringRepository(session).get_latest_result(
                tenant_id=tenant_id, lead_id=lead_id
            )
        if rec is None:
            raise ProvenanceNotFound(tenant_id, lead_id)
        breakdown = rec.breakdown if isinstance(rec.breakdown, dict) else {}
        capture = (breakdown.get("provenance") or {}).get("confidence_capture")
        if not capture:
            raise HTTPException(
                status_code=422,
                detail=f"Score for lead {lead_id!r} has no confidence capture recorded",
            )
        return capture


def _extract(breakdown: Any, tenant_id: str, lead_id: str) -> ScoringProvenance:
    prov = (breakdown or {}).get("provenance") if isinstance(breakdown, dict) else None
    if not prov:
        # a score exists but predates provenance capture
        raise HTTPException(
            status_code=422,
            detail=f"Score for lead {lead_id!r} has no provenance recorded",
        )
    return ScoringProvenance.from_storage(prov)


# --------------------------------------------------------------------------- #
# API                                                                          #
# --------------------------------------------------------------------------- #
def get_provenance_service() -> ProvenanceService:  # pragma: no cover - wired at startup
    raise RuntimeError(
        "ProvenanceService dependency not wired. Override at app startup."
    )


router = APIRouter(prefix="/scoring", tags=["provenance"])


@router.get("/{tenant_id}/leads/{lead_id}/provenance", response_model=ScoringProvenance)
async def get_provenance(
    tenant_id: str,
    lead_id: str,
    service: ProvenanceService = Depends(get_provenance_service),
) -> ScoringProvenance:
    try:
        return await service.get_latest(tenant_id=tenant_id, lead_id=lead_id)
    except ProvenanceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/{tenant_id}/leads/{lead_id}/provenance/history",
    response_model=list[ScoringProvenance],
)
async def get_provenance_history(
    tenant_id: str,
    lead_id: str,
    service: ProvenanceService = Depends(get_provenance_service),
) -> list[ScoringProvenance]:
    try:
        return await service.get_history(tenant_id=tenant_id, lead_id=lead_id)
    except ProvenanceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/{tenant_id}/leads/{lead_id}/confidence",
    response_model=ConfidenceCapture,
)
async def get_confidence(
    tenant_id: str,
    lead_id: str,
    service: ProvenanceService = Depends(get_provenance_service),
) -> ConfidenceCapture:
    """COMP-1203-ST4 — the traceable confidence record for a lead's latest score."""
    try:
        data = await service.get_confidence(tenant_id=tenant_id, lead_id=lead_id)
    except ProvenanceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ConfidenceCapture.model_validate(data)
