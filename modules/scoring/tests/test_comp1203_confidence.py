"""
COMP-1203-ST4 — Confidence capture test suite (Epic 6, Domain 12).

Covers the model (banding, recompute inputs, validation), the service writing a
traceable capture alongside provenance, and the retrieval endpoint.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes_api import provenance_routes as prov_api
from db.repository import ScoringRepository
from schemas.confidence import (
    ConfidenceCapture,
    ConfidenceLevel,
    ConfidenceMethod,
)

# model tests are sync; DB/service tests are marked individually below.


# --- model ----------------------------------------------------------------- #
def test_level_banding():
    assert ConfidenceCapture.level_for(0.95) is ConfidenceLevel.HIGH
    assert ConfidenceCapture.level_for(0.6) is ConfidenceLevel.MEDIUM
    assert ConfidenceCapture.level_for(0.3) is ConfidenceLevel.LOW


def test_from_value_captures_recompute_inputs():
    cap = ConfidenceCapture.from_value(
        0.6, total_signals=10, skipped_signals=4, triggered_fallback=True
    )
    assert cap.level is ConfidenceLevel.MEDIUM
    assert cap.method is ConfidenceMethod.SKIPPED_SIGNAL_RATIO
    assert cap.skipped_ratio == 0.4
    assert cap.triggered_fallback is True


def test_skipped_cannot_exceed_total():
    with pytest.raises(ValueError):
        ConfidenceCapture.from_value(0.5, total_signals=3, skipped_signals=5)


def test_skipped_ratio_none_without_counts():
    cap = ConfidenceCapture.from_value(0.9)
    assert cap.skipped_ratio is None


# --- service writes a capture alongside provenance ------------------------- #
@pytest.mark.asyncio
async def test_service_captures_confidence(service, seeded_factory):
    await service.score_lead(tenant_id="venturedesk", features={"lead_id": "L-HOT"})
    async with seeded_factory() as s:
        rec = await ScoringRepository(s).get_latest_result(
            tenant_id="venturedesk", lead_id="L-HOT"
        )
    cap_data = rec.breakdown["provenance"]["confidence_capture"]
    cap = ConfidenceCapture.model_validate(cap_data)
    assert cap.level is ConfidenceLevel.HIGH  # L-HOT scores 0.95
    assert cap.triggered_fallback is False


@pytest.mark.asyncio
async def test_service_captures_fallback_confidence(service, seeded_factory):
    await service.score_lead(tenant_id="venturedesk", features={"lead_id": "L-SPARSE"})
    async with seeded_factory() as s:
        rec = await ScoringRepository(s).get_latest_result(
            tenant_id="venturedesk", lead_id="L-SPARSE"
        )
    cap = ConfidenceCapture.model_validate(
        rec.breakdown["provenance"]["confidence_capture"]
    )
    # after the fallback runs, the rescored confidence is high (0.9) but the
    # capture records that the fallback was triggered
    assert cap.triggered_fallback is True


# --- retrieval endpoint ---------------------------------------------------- #
@pytest.fixture
def conf_client(seeded_factory, service):
    import asyncio

    async def _seed():
        await service.score_lead(tenant_id="venturedesk", features={"lead_id": "L-HOT"})

    asyncio.get_event_loop().run_until_complete(_seed())
    svc = prov_api.ProvenanceService(seeded_factory)
    app = FastAPI()
    app.include_router(prov_api.router)
    app.dependency_overrides[prov_api.get_provenance_service] = lambda: svc
    return TestClient(app)


def test_confidence_endpoint(conf_client):
    r = conf_client.get("/scoring/venturedesk/leads/L-HOT/confidence")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["level"] == "high"
    assert body["method"] == "skipped_signal_ratio"


def test_confidence_endpoint_404(conf_client):
    r = conf_client.get("/scoring/venturedesk/leads/NOPE/confidence")
    assert r.status_code == 404
