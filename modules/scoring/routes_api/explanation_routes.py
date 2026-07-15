"""
COMP-1202-ST5 / ST7 — Explainability API & reporting (Epic 6, Domain 12)

ST5: read endpoints that return a ScoreExplanation for a scored lead, generated
deterministically from the stored breakdown (no engine re-run, no LLM call).
ST7: a tenant-level reporting endpoint that rolls explanations up into an audit-
friendly summary (bucket distribution, how many scores were LLM-assisted, common
detractors) — the kind of artifact a Compliance Manager exports.

ST6 (UI components) is Epic 7's render surface; it consumes the ScoreExplanation
JSON these endpoints return. Nothing here renders HTML.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.repository import ScoringRepository
from schemas.explanation import ExplanationGenerator, ScoreExplanation


class ExplanationNotFound(Exception):
    def __init__(self, tenant_id: str, lead_id: str) -> None:
        super().__init__(f"No scored result for lead {lead_id!r} (tenant {tenant_id!r})")


class ExplainabilityReport(BaseModel):
    """ST7 — tenant-level rollup for audit."""

    tenant_id: str
    total_leads: int
    bucket_distribution: dict[str, int]
    llm_assisted_count: int
    blocked_count: int
    common_detractors: list[str]


class ExplainabilityService:
    """Read-side: generate explanations and reports from stored scores."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        generator: ExplanationGenerator | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._gen = generator or ExplanationGenerator()

    async def explain_lead(self, *, tenant_id: str, lead_id: str) -> ScoreExplanation:
        async with self._session_factory() as session:
            rec = await ScoringRepository(session).get_latest_result(
                tenant_id=tenant_id, lead_id=lead_id
            )
        if rec is None:
            raise ExplanationNotFound(tenant_id, lead_id)
        return self._gen.explain(_as_breakdown(rec))

    async def report(self, *, tenant_id: str, lead_ids: list[str]) -> ExplainabilityReport:
        buckets: Counter[str] = Counter()
        detractors: Counter[str] = Counter()
        llm_assisted = 0
        blocked = 0
        seen = 0

        async with self._session_factory() as session:
            repo = ScoringRepository(session)
            for lead_id in lead_ids:
                rec = await repo.get_latest_result(tenant_id=tenant_id, lead_id=lead_id)
                if rec is None:
                    continue
                exp = self._gen.explain(_as_breakdown(rec))
                seen += 1
                buckets[exp.classification.lower()] += 1
                if exp.llm_assisted:
                    llm_assisted += 1
                if exp.blocked:
                    blocked += 1
                for d in exp.top_detractors:
                    detractors[d] += 1

        return ExplainabilityReport(
            tenant_id=tenant_id,
            total_leads=seen,
            bucket_distribution=dict(buckets),
            llm_assisted_count=llm_assisted,
            blocked_count=blocked,
            common_detractors=[d for d, _ in detractors.most_common(5)],
        )


def _as_breakdown(record: Any) -> dict[str, Any]:
    """Normalise a ScoringResultRecord into the breakdown dict the generator
    expects, ensuring the top-level score fields are present even if the stored
    breakdown only held the nested detail."""
    breakdown = dict(record.breakdown or {})
    breakdown.setdefault("lead_id", record.lead_id)
    breakdown.setdefault("score", record.score)
    breakdown.setdefault("classification", record.classification)
    return breakdown


# --------------------------------------------------------------------------- #
# API                                                                          #
# --------------------------------------------------------------------------- #
def get_explainability_service() -> ExplainabilityService:  # pragma: no cover
    raise RuntimeError("ExplainabilityService dependency not wired. Override at startup.")


router = APIRouter(prefix="/scoring", tags=["explainability"])


@router.get(
    "/{tenant_id}/leads/{lead_id}/explanation", response_model=ScoreExplanation
)
async def get_explanation(
    tenant_id: str,
    lead_id: str,
    service: ExplainabilityService = Depends(get_explainability_service),
) -> ScoreExplanation:
    try:
        return await service.explain_lead(tenant_id=tenant_id, lead_id=lead_id)
    except ExplanationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class ReportRequest(BaseModel):
    lead_ids: list[str]


@router.post("/{tenant_id}/explanation-report", response_model=ExplainabilityReport)
async def post_explanation_report(
    tenant_id: str,
    body: ReportRequest,
    service: ExplainabilityService = Depends(get_explainability_service),
) -> ExplainabilityReport:
    return await service.report(tenant_id=tenant_id, lead_ids=body.lead_ids)
