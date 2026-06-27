"""
COMP-1201-ST8 — Provenance test suite (Epic 6, Domain 12).

Covers:
- the typed model's validation rules (LLM versions required iff LLM scored),
- storage round-trip through breakdown JSONB,
- the service writing valid provenance on both the deterministic and the
  agent-owned LLM-fallback paths,
- the retrieval API returning it (and 404 / 422 edge cases).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes_api import provenance_routes as prov_api
from db.repository import ScoringRepository
from schemas.provenance import EvidenceSource, ScorerType, ScoringProvenance

# async DB/service tests get the mark individually; pure-model and
# TestClient tests are synchronous.


# --- model validation ------------------------------------------------------ #
def test_deterministic_provenance_is_valid_without_llm_versions():
    p = ScoringProvenance(
        scorer=ScorerType.DETERMINISTIC, signal_set_version=1, confidence=0.95
    )
    assert p.prompt_version is None and p.model_version is None


def test_llm_provenance_requires_versions():
    with pytest.raises(ValueError):
        ScoringProvenance(
            scorer=ScorerType.LLM_FALLBACK, signal_set_version=1, confidence=0.6
        )


def test_deterministic_provenance_rejects_llm_versions():
    with pytest.raises(ValueError):
        ScoringProvenance(
            scorer=ScorerType.DETERMINISTIC,
            signal_set_version=1,
            confidence=0.95,
            model_version="claude-sonnet-4-6",
        )


def test_storage_round_trip():
    p = ScoringProvenance(
        scorer=ScorerType.LLM_FALLBACK,
        signal_set_version=2,
        signal_set_source="onboarding-7",
        signal_names=["uses_crm"],
        confidence=0.9,
        prompt_version="signal-v3",
        model_version="claude-sonnet-4-6",
        evidence_sources=[EvidenceSource(field="headcount", source="serper", confidence=0.8)],
    )
    restored = ScoringProvenance.from_storage(p.to_storage())
    assert restored == p


# --- service writes valid provenance --------------------------------------- #
@pytest.mark.asyncio
async def test_service_writes_deterministic_provenance(service, seeded_factory):
    await service.score_lead(tenant_id="venturedesk", features={"lead_id": "L-HOT"})
    async with seeded_factory() as s:
        rec = await ScoringRepository(s).get_latest_result(
            tenant_id="venturedesk", lead_id="L-HOT"
        )
    p = ScoringProvenance.from_storage(rec.breakdown["provenance"])
    assert p.scorer is ScorerType.DETERMINISTIC
    assert p.signal_set_version == 1
    assert p.signal_names == ["uses_crm", "headcount"]


@pytest.mark.asyncio
async def test_service_writes_llm_provenance_with_versions(service, seeded_factory):
    await service.score_lead(tenant_id="venturedesk", features={"lead_id": "L-SPARSE"})
    async with seeded_factory() as s:
        rec = await ScoringRepository(s).get_latest_result(
            tenant_id="venturedesk", lead_id="L-SPARSE"
        )
    p = ScoringProvenance.from_storage(rec.breakdown["provenance"])
    assert p.scorer is ScorerType.LLM_FALLBACK
    assert p.prompt_version == "signal-v3"
    assert p.model_version == "claude-sonnet-4-6"


# --- retrieval API --------------------------------------------------------- #
@pytest.fixture
def prov_client(seeded_factory, service):
    # score two leads so there's provenance to retrieve
    import asyncio

    async def _seed():
        await service.score_lead(tenant_id="venturedesk", features={"lead_id": "L-HOT"})
        await service.score_lead(tenant_id="venturedesk", features={"lead_id": "L-SPARSE"})

    asyncio.get_event_loop().run_until_complete(_seed())

    svc = prov_api.ProvenanceService(seeded_factory)
    app = FastAPI()
    app.include_router(prov_api.router)
    app.dependency_overrides[prov_api.get_provenance_service] = lambda: svc
    return TestClient(app)


def test_get_provenance_endpoint(prov_client):
    r = prov_client.get("/scoring/venturedesk/leads/L-SPARSE/provenance")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scorer"] == "llm_fallback"
    assert body["model_version"] == "claude-sonnet-4-6"


def test_get_provenance_404_for_unknown_lead(prov_client):
    r = prov_client.get("/scoring/venturedesk/leads/NOPE/provenance")
    assert r.status_code == 404
