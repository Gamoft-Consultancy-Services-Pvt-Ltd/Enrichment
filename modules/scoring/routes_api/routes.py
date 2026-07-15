"""
LEAD-56 — Scoring API (Epic 6: Lead Scoring Runtime)

Thin FastAPI layer over ScoringService (LEAD-55). It does request/response
shaping and dependency wiring only — no scoring logic, no DB access of its own.

Endpoints:
- POST /scoring/{tenant_id}/score   : score one lead synchronously.
- GET  /scoring/{tenant_id}/leads/{lead_id}  : fetch the latest stored score.
- POST /scoring/{tenant_id}/invalidate-config : drop the cached SignalSet
  (called by onboarding after it writes a new config).

Design choices:
- The service is provided via FastAPI dependency injection (get_service), so
  tests can override it with a fake and production wires the real one at app
  startup. The route handlers never construct collaborators themselves.
- SignalSetNotConfigured maps to 409 Conflict (the tenant exists but isn't
  ready to score), not 404 — a 404 would wrongly imply the lead/endpoint is
  missing.
- Response models are explicit Pydantic schemas so the OpenAPI contract is
  stable for the co-intern's upstream caller and for the worker.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from services.service import ScoringService, SignalSetNotConfigured


# --------------------------------------------------------------------------- #
# Request / response schemas                                                   #
# --------------------------------------------------------------------------- #
class ScoreRequest(BaseModel):
    lead_id: str = Field(..., description="Tenant-scoped lead identifier")
    features: dict[str, Any] = Field(
        ..., description="Enriched lead features (LeadFeatures payload)"
    )


class ScoreResponse(BaseModel):
    lead_id: str
    score: float
    classification: str
    confidence: Any
    llm_adjusted: bool
    record_id: int


class StoredScoreResponse(BaseModel):
    lead_id: str
    score: float
    classification: str
    llm_adjusted: bool
    breakdown: dict[str, Any]


# --------------------------------------------------------------------------- #
# Dependency seam — overridden in tests, wired at startup in prod             #
# --------------------------------------------------------------------------- #
def get_service() -> ScoringService:  # pragma: no cover - replaced at runtime
    raise RuntimeError(
        "ScoringService dependency not wired. Override get_service at app startup."
    )


router = APIRouter(prefix="/scoring", tags=["scoring"])


@router.post("/{tenant_id}/score", response_model=ScoreResponse)
async def score_lead(
    tenant_id: str,
    body: ScoreRequest,
    service: ScoringService = Depends(get_service),
) -> ScoreResponse:
    features = {"lead_id": body.lead_id, **body.features}
    try:
        outcome = await service.score_lead(tenant_id=tenant_id, features=features)
    except SignalSetNotConfigured as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    result = outcome.result
    return ScoreResponse(
        lead_id=body.lead_id,
        score=_attr(result, "score", 0.0),
        classification=_attr(result, "classification", "cold"),
        confidence=_attr(result, "confidence", None),
        llm_adjusted=outcome.from_llm_fallback,
        record_id=outcome.record_id,
    )


@router.post("/{tenant_id}/invalidate-config", status_code=204)
async def invalidate_config(
    tenant_id: str,
    service: ScoringService = Depends(get_service),
) -> None:
    await service.invalidate_signal_set(tenant_id)


def _attr(obj: Any, name: str, default: Any) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)
